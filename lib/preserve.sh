#!/usr/bin/env bash
# lib/preserve.sh — checksummed, durable preservation for updater checkouts.
# Sourced by update.sh. Callers provide log(), warn(), and die().

# A single unexpectedly large untracked tree must not fill the disk while the
# updater is trying to protect it. This is deliberately a product constant,
# not an environment override that can silently weaken the safety boundary.
_PLEB_PRESERVE_MAX_BYTES=1073741824       # 1 GiB per checkout snapshot
_PLEB_PRESERVE_FREE_RESERVE=67108864      # leave at least 64 MiB free
_PLEB_PRESERVE_KEEP=10                    # verified snapshots per checkout
# shellcheck disable=SC2034 # public result channel for update.sh callers
PLEB_PRESERVE_RESULT=""

_pleb_preservation_root() {
    printf '%s\n' "$PLEB_STATE_HOME/update-preserve"
}

_pleb_assert_preservation_root_outside_checkouts() {
    local root dir resolved
    root="$(readlink -m -- "$(_pleb_preservation_root)")" \
        || die "could not resolve the Pleb preservation root"
    for dir in "$PLEB_ROOT" "${PLEB_DIR:-}" "$KILIX_DIR" "$KILIX95_DIR" \
            "$GPU_TERMINAL_SOURCE_HOME"; do
        [ -n "$dir" ] || continue
        resolved="$(readlink -m -- "$dir")" \
            || die "could not resolve participating checkout path: $dir"
        case "$root" in
            "$resolved"|"$resolved"/*)
                die "update preservation root must be outside participating checkouts: $root" ;;
        esac
        case "$resolved" in
            "$root"|"$root"/*)
                die "participating checkout must be outside the update preservation root: $resolved" ;;
        esac
    done
}

# Python is used here because Git permits whitespace, newlines, and non-UTF-8
# bytes in pathnames. Keeping the helper embedded means a Pleb self-update
# cannot replace the helper halfway through the running shell's restore path.
_pleb_preserve_python() {
    python3 - "$@" <<'PY'
import base64
import datetime
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys


class PreserveError(RuntimeError):
    pass


def b64(value):
    return base64.b64encode(value).decode("ascii")


def unb64(value):
    return base64.b64decode(value.encode("ascii"), validate=True)


def run_git(checkout, args, check=True):
    command = [b"git", b"-C", checkout]
    command.extend(arg if isinstance(arg, bytes) else os.fsencode(arg) for arg in args)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        detail = os.fsdecode(result.stderr.strip()) or "git command failed"
        raise PreserveError(detail)
    return result


def fsync_file(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_tree(root):
    root_b = os.fsencode(root)
    for current, dirs, files in os.walk(root_b, topdown=False, followlinks=False):
        for name in files:
            path = os.path.join(current, name)
            if stat.S_ISREG(os.lstat(path).st_mode):
                fsync_file(path)
        fsync_dir(current)


def fsync_output(path, boundary):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISREG(st.st_mode):
        fsync_file(path)
    elif stat.S_ISDIR(st.st_mode):
        fsync_tree(path)
    current = os.path.dirname(path)
    while current.startswith(boundary):
        fsync_dir(current)
        if current == boundary:
            break
        current = os.path.dirname(current)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb", buffering=0) as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def validate_relative(path):
    if not path or path.startswith(b"/") or b"\0" in path:
        raise PreserveError("Git returned an unsafe checkout path")
    components = path.split(b"/")
    if any(component in (b"", b".", b"..") for component in components):
        raise PreserveError("Git returned a non-normal checkout path")


def assert_no_symlink_components(path):
    if not os.path.isabs(path):
        raise PreserveError("checkout path is not absolute")
    current = b"/"
    for component in path.split(b"/"):
        if not component:
            continue
        current = os.path.join(current, component)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            raise PreserveError("checkout path disappeared during preservation")
        if stat.S_ISLNK(st.st_mode):
            raise PreserveError("checkout path gained a symlink component")


def changed_paths(checkout):
    raw = run_git(
        checkout,
        [
            b"status", b"--porcelain=v1", b"-z", b"--untracked-files=all",
            b"--ignore-submodules=all",
        ],
    ).stdout
    fields = raw.split(b"\0")
    kinds = {}
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 4 or field[2:3] != b" ":
            raise PreserveError("Git returned malformed porcelain status")
        xy = field[:2]
        path = field[3:]
        validate_relative(path)
        kinds[path] = "untracked" if xy == b"??" else "tracked"
        if b"R" in xy or b"C" in xy:
            if index >= len(fields) or not fields[index]:
                raise PreserveError("Git returned an incomplete rename status")
            original = fields[index]
            index += 1
            validate_relative(original)
            if b"R" in xy:
                kinds[original] = "tracked"
    return kinds


def porcelain(checkout):
    return run_git(
        checkout,
        [b"status", b"--porcelain=v1", b"--untracked-files=all", b"--ignore-submodules=all"],
    ).stdout


def head_of(checkout):
    return run_git(checkout, [b"rev-parse", b"--verify", b"HEAD"]).stdout.strip().decode("ascii")


def index_state(checkout, path):
    result = run_git(checkout, [b"ls-files", b"--stage", b"-z", b"--", path]).stdout
    records = []
    for raw in result.split(b"\0"):
        if not raw:
            continue
        prefix, recorded_path = raw.split(b"\t", 1)
        mode, oid, stage_number = prefix.split(b" ")
        if recorded_path == path:
            records.append((mode.decode("ascii"), oid.decode("ascii"), int(stage_number)))
    if any(stage_number != 0 for _, _, stage_number in records):
        return {"unmerged": True, "records": records}
    if not records:
        return {"present": False}
    mode, oid, _ = records[0]
    return {"present": True, "mode": mode, "oid": oid}


def kind_for_mode(mode):
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    raise PreserveError("refusing to preserve a socket, device, or other special path")


def scan_node(path, relative, nodes, byte_total):
    st = os.lstat(path)
    kind = kind_for_mode(st.st_mode)
    record = {
        "path_b64": b64(relative),
        "kind": kind,
        "mode": stat.S_IMODE(st.st_mode),
        "mtime_ns": st.st_mtime_ns,
    }
    if kind == "file":
        record["size"] = st.st_size
        record["sha256"] = sha256_file(path)
        byte_total[0] += st.st_size
    elif kind == "symlink":
        target = os.readlink(path)
        target_b = target if isinstance(target, bytes) else os.fsencode(target)
        record["target_b64"] = b64(target_b)
        byte_total[0] += len(target_b)
    nodes.append(record)
    if kind == "directory":
        with os.scandir(path) as entries:
            names = sorted((entry.name for entry in entries))
        for name in names:
            name_b = name if isinstance(name, bytes) else os.fsencode(name)
            scan_node(os.path.join(path, name_b), relative + b"/" + name_b, nodes, byte_total)


def scan_roots(base, roots):
    nodes = []
    byte_total = [0]
    absent = []
    for relative in sorted(roots):
        path = os.path.join(base, relative)
        try:
            scan_node(path, relative, nodes, byte_total)
        except FileNotFoundError:
            absent.append(b64(relative))
    nodes.sort(key=lambda item: item["path_b64"])
    return nodes, absent, byte_total[0]


def safe_parent(root, relative):
    current = root
    for component in relative.split(b"/")[:-1]:
        current = os.path.join(current, component)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current, 0o700)
            continue
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
            raise PreserveError("unsafe parent while constructing preservation tree")


def copy_node(source, destination):
    st = os.lstat(source)
    kind = kind_for_mode(st.st_mode)
    if kind == "symlink":
        os.symlink(os.readlink(source), destination)
        try:
            shutil.copystat(source, destination, follow_symlinks=False)
        except NotImplementedError:
            pass
    elif kind == "directory":
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def copy_roots(checkout, destination_root, roots):
    os.mkdir(destination_root, 0o700)
    for relative in sorted(roots):
        source = os.path.join(checkout, relative)
        try:
            os.lstat(source)
        except FileNotFoundError:
            continue
        destination = os.path.join(destination_root, relative)
        safe_parent(destination_root, relative)
        copy_node(source, destination)


def canonical_json(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_file(path, content, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def manifest_escape(path):
    needs_escape = b"\\" in path or b"\n" in path
    escaped = path.replace(b"\\", b"\\\\").replace(b"\n", b"\\n")
    return needs_escape, escaped


def write_checksum_manifest(snapshot):
    records = []
    for current, dirs, files in os.walk(snapshot, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not os.path.islink(os.path.join(current, name)))
        for name in sorted(files):
            path = os.path.join(current, name)
            if os.path.islink(path) or name == b"MANIFEST.sha256":
                continue
            relative = os.path.relpath(path, snapshot)
            records.append((relative, sha256_file(path)))
    content = bytearray()
    for relative, digest in sorted(records):
        escaped, rendered = manifest_escape(relative)
        content.extend(
            (b"\\" if escaped else b"")
            + digest.encode("ascii") + b"  " + rendered + b"\n"
        )
    write_file(os.path.join(snapshot, b"MANIFEST.sha256"), bytes(content))


def read_metadata(snapshot):
    with open(os.path.join(snapshot, b"METADATA.json"), "rb") as source:
        metadata = json.load(source)
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise PreserveError("unsupported preservation metadata schema")
    entries = metadata.get("entries")
    nodes = metadata.get("nodes")
    absent = metadata.get("absent")
    if not isinstance(entries, list) or not isinstance(nodes, list) \
            or not isinstance(absent, list):
        raise PreserveError("invalid preservation metadata shape")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("classification") not in (
                "tracked", "untracked"):
            raise PreserveError("invalid preservation entry")
        path = unb64(entry.get("path_b64", ""))
        validate_relative(path)
        if path in seen:
            raise PreserveError("duplicate preservation entry")
        seen.add(path)
    for node in nodes:
        if not isinstance(node, dict):
            raise PreserveError("invalid preservation node")
        validate_relative(unb64(node.get("path_b64", "")))
    for encoded in absent:
        validate_relative(unb64(encoded))
    checkout = unb64(metadata.get("checkout_b64", ""))
    if not os.path.isabs(checkout):
        raise PreserveError("preservation checkout identity is not absolute")
    return metadata


def scan_copied_files(snapshot, metadata):
    roots = [unb64(entry["path_b64"]) for entry in metadata["entries"]]
    return scan_roots(os.path.join(snapshot, b"files"), roots)[:2]


def verify_checksums(snapshot):
    manifest_path = os.path.join(snapshot, b"MANIFEST.sha256")
    with open(manifest_path, "rb") as source:
        lines = source.read().splitlines()
    for line in lines:
        escaped = line.startswith(b"\\")
        if escaped:
            line = line[1:]
        if len(line) < 67 or line[64:66] != b"  ":
            raise PreserveError("invalid preservation checksum manifest")
        digest = line[:64].decode("ascii")
        relative = line[66:]
        if escaped:
            rendered = bytearray()
            index = 0
            while index < len(relative):
                if relative[index:index + 1] != b"\\":
                    rendered.extend(relative[index:index + 1])
                    index += 1
                    continue
                if index + 1 >= len(relative):
                    raise PreserveError("invalid checksum pathname escape")
                escaped_byte = relative[index + 1:index + 2]
                if escaped_byte == b"n":
                    rendered.extend(b"\n")
                elif escaped_byte == b"\\":
                    rendered.extend(b"\\")
                else:
                    raise PreserveError("invalid checksum pathname escape")
                index += 2
            relative = bytes(rendered)
        validate_relative(relative)
        path = os.path.join(snapshot, relative)
        if os.path.islink(path) or not os.path.isfile(path):
            raise PreserveError("preservation checksum names a non-regular file")
        if sha256_file(path) != digest:
            raise PreserveError("preservation checksum verification failed")


def verify_snapshot(snapshot):
    metadata = read_metadata(snapshot)
    verify_checksums(snapshot)
    nodes, absent = scan_copied_files(snapshot, metadata)
    if nodes != metadata["nodes"] or absent != metadata["absent"]:
        raise PreserveError("preserved tree does not match its metadata")
    status_path = os.path.join(snapshot, b"STATUS")
    with open(status_path, "rb") as source:
        status = source.read()
    if hashlib.sha256(status).hexdigest() != metadata["status_file_sha256"]:
        raise PreserveError("preservation STATUS verification failed")
    return metadata


def status_content(checkout, label, phase, head, raw):
    header = (
        "schema: pleb.update-preserve/v1\n"
        f"checkout: {os.fsdecode(checkout)}\n"
        f"label: {label}\n"
        f"phase: {phase}\n"
        f"head: {head}\n"
        "porcelain-v1:\n"
    ).encode("utf-8", "backslashreplace")
    return header + raw


def snapshot_command(args):
    checkout = os.fsencode(args[0])
    root = os.fsencode(args[1])
    label = args[2]
    phase = args[3]
    max_bytes = int(args[4])
    reserve = int(args[5])
    keep = int(args[6])
    assert_no_symlink_components(checkout)
    kinds = changed_paths(checkout)
    raw_status = porcelain(checkout)
    if not kinds and not raw_status:
        return
    roots = sorted(kinds)
    nodes, absent, byte_total = scan_roots(checkout, roots)
    entries = []
    index_bytes = 0
    index_payloads = []
    for path in roots:
        index = index_state(checkout, path) if kinds[path] == "tracked" else {"present": False}
        entry = {"path_b64": b64(path), "classification": kinds[path], "index": index}
        entries.append(entry)
        if index.get("present") and index.get("mode") != "160000":
            payload = run_git(checkout, [b"cat-file", b"blob", index["oid"]]).stdout
            index_payloads.append((path, payload))
            index_bytes += len(payload)
    total = byte_total + index_bytes
    if total > max_bytes:
        raise PreserveError(
            f"checkout changes require {total} bytes, exceeding the {max_bytes}-byte preservation ceiling"
        )
    free = shutil.disk_usage(root).free
    # Preparing a checkout on another filesystem cannot use a same-filesystem
    # rename into the snapshot's aside directory, so budget a second copy.
    copy_factor = 1 if os.stat(checkout).st_dev == os.stat(root).st_dev else 2
    required_free = total * copy_factor + reserve
    if free < required_free:
        raise PreserveError(
            f"preservation needs {required_free} free bytes including reserve; only {free} are available"
        )
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    safe_label = "".join(character if character.isalnum() else "-" for character in label).strip("-")
    safe_phase = "".join(character if character.isalnum() else "-" for character in phase).strip("-")
    final_name = f"{timestamp}-{safe_phase}-{safe_label}"
    final = os.path.join(root, os.fsencode(final_name))
    collision = 0
    while os.path.lexists(final):
        collision += 1
        final_name = f"{timestamp}.{collision:02d}-{safe_phase}-{safe_label}"
        final = os.path.join(root, os.fsencode(final_name))
    temporary = os.path.join(root, os.fsencode(f".{final_name}.incomplete-{os.getpid()}"))
    os.mkdir(temporary, 0o700)
    try:
        copy_roots(checkout, os.path.join(temporary, b"files"), roots)
        os.mkdir(os.path.join(temporary, b"index"), 0o700)
        for path, payload in index_payloads:
            destination = os.path.join(temporary, b"index", path)
            safe_parent(os.path.join(temporary, b"index"), path)
            write_file(destination, payload)
        copied_nodes, copied_absent, _ = scan_roots(os.path.join(temporary, b"files"), roots)
        origin_nodes, origin_absent, _ = scan_roots(checkout, roots)
        if copied_nodes != nodes or copied_absent != absent or origin_nodes != nodes or origin_absent != absent:
            raise PreserveError("checkout changed while it was being preserved")
        head = head_of(checkout)
        if porcelain(checkout) != raw_status:
            raise PreserveError("checkout status changed while it was being preserved")
        status = status_content(checkout, label, phase, head, raw_status)
        write_file(os.path.join(temporary, b"STATUS"), status)
        metadata = {
            "schema_version": 1,
            "checkout_b64": b64(checkout),
            "label": label,
            "phase": phase,
            "head": head,
            "entries": entries,
            "nodes": nodes,
            "absent": absent,
            "preserved_bytes": total,
            "status_sha256": hashlib.sha256(raw_status).hexdigest(),
            "status_file_sha256": hashlib.sha256(status).hexdigest(),
        }
        write_file(os.path.join(temporary, b"METADATA.json"), canonical_json(metadata))
        write_checksum_manifest(temporary)
        verify_snapshot(temporary)
        fsync_tree(temporary)
        os.rename(temporary, final)
        fsync_dir(root)
        prune_command([os.fsdecode(root), str(keep), label])
        print(os.fsdecode(final))
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        try:
            fsync_dir(root)
        except OSError:
            pass
        raise


def snapshot_raw_status(snapshot):
    with open(os.path.join(snapshot, b"STATUS"), "rb") as source:
        content = source.read()
    marker = b"porcelain-v1:\n"
    position = content.find(marker)
    if position < 0:
        raise PreserveError("preservation STATUS has no porcelain marker")
    return content[position + len(marker):]


def remove_or_park(source, aside_root, relative):
    try:
        os.lstat(source)
    except FileNotFoundError:
        return
    destination = os.path.join(aside_root, relative)
    safe_parent(aside_root, relative)
    try:
        os.rename(source, destination)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        copy_node(source, destination)
        fsync_tree(destination) if os.path.isdir(destination) else fsync_file(destination)
        if os.path.islink(source) or not os.path.isdir(source):
            os.unlink(source)
        else:
            shutil.rmtree(source)


def reset_paths_to_head(checkout, entries):
    for entry in entries:
        if entry["classification"] != "tracked":
            continue
        path = unb64(entry["path_b64"])
        run_git(checkout, [b"reset", b"-q", b"HEAD", b"--", path])
        result = run_git(checkout, [b"checkout", b"-q", b"HEAD", b"--", path], check=False)
        if result.returncode:
            present = run_git(checkout, [b"ls-tree", b"-z", b"HEAD", b"--", path]).stdout
            if present:
                raise PreserveError("could not restore a tracked path to HEAD before update")


def prepare_command(args):
    checkout = os.fsencode(args[0])
    snapshot = os.fsencode(args[1])
    assert_no_symlink_components(checkout)
    metadata = verify_snapshot(snapshot)
    if os.fsencode(unb64(metadata["checkout_b64"])) != checkout:
        raise PreserveError("preservation checkout identity does not match")
    roots = [unb64(entry["path_b64"]) for entry in metadata["entries"]]
    nodes, absent, _ = scan_roots(checkout, roots)
    if nodes != metadata["nodes"] or absent != metadata["absent"]:
        raise PreserveError("checkout content changed after preservation")
    raw_status = porcelain(checkout)
    if hashlib.sha256(raw_status).hexdigest() != metadata["status_sha256"]:
        raise PreserveError("checkout status changed after preservation")
    if any(entry["index"].get("unmerged") for entry in metadata["entries"]):
        raise PreserveError("cannot update a checkout with unresolved index conflicts")
    aside = os.path.join(snapshot, b"aside")
    os.mkdir(aside, 0o700)
    for relative in roots:
        remove_or_park(os.path.join(checkout, relative), aside, relative)
    reset_paths_to_head(checkout, metadata["entries"])
    if porcelain(checkout):
        raise PreserveError("checkout was not clean after its local paths were preserved")
    write_file(os.path.join(snapshot, b"PREPARED"), b"prepared\n")
    fsync_tree(snapshot)


def unique_sidecar(path, suffix):
    candidate = path + suffix
    counter = 1
    while os.path.lexists(candidate):
        candidate = path + suffix + b"." + str(counter).encode("ascii")
        counter += 1
    return candidate


def displace(path, snapshot, relative):
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    displaced_root = os.path.join(snapshot, b"restore-displaced")
    displaced = os.path.join(displaced_root, relative)
    safe_parent(displaced_root, relative)
    if os.path.lexists(displaced):
        displaced = unique_sidecar(displaced, b".later")
    try:
        os.rename(path, displaced)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        copy_node(path, displaced)
        fsync_output(displaced, snapshot)
        if os.path.islink(path) or not os.path.isdir(path):
            os.unlink(path)
        else:
            shutil.rmtree(path)


def restore_index(checkout, entries):
    for entry in entries:
        if entry["classification"] != "tracked":
            continue
        path = unb64(entry["path_b64"])
        run_git(checkout, [b"reset", b"-q", b"HEAD", b"--", path])
        index = entry["index"]
        if index.get("unmerged"):
            raise PreserveError("cannot automatically restore an unmerged index")
        if index.get("present"):
            run_git(
                checkout,
                [b"update-index", b"--add", b"--cacheinfo", index["mode"], index["oid"], path],
            )
        else:
            run_git(checkout, [b"update-index", b"--force-remove", b"--", path])


def restore_command(args):
    checkout = os.fsencode(args[0])
    snapshot = os.fsencode(args[1])
    mode = args[2]
    target_sha = args[3]
    assert_no_symlink_components(checkout)
    metadata = verify_snapshot(snapshot)
    entries = metadata["entries"]
    files = os.path.join(snapshot, b"files")
    if not os.path.isdir(checkout) or os.path.islink(checkout):
        raise PreserveError("preserved checkout root is no longer a safe directory")
    if mode == "rollback":
        displaced_root = os.path.join(snapshot, b"restore-displaced")
        os.makedirs(displaced_root, mode=0o700, exist_ok=True)
        for entry in entries:
            relative = unb64(entry["path_b64"])
            target = os.path.join(checkout, relative)
            displace(target, snapshot, relative)
            source = os.path.join(files, relative)
            if os.path.lexists(source):
                safe_parent(checkout, relative)
                copy_node(source, target)
        restore_index(checkout, entries)
        if porcelain(checkout) != snapshot_raw_status(snapshot):
            raise PreserveError("rollback did not reproduce the preserved checkout status")
        for entry in entries:
            fsync_output(
                os.path.join(checkout, unb64(entry["path_b64"])), checkout
            )
        return
    if mode != "success":
        raise PreserveError("unknown preservation restore mode")
    short_sha = target_sha[:12].encode("ascii")
    restored = []
    ordered_entries = [
        entry for entry in entries if entry["classification"] == "untracked"
    ] + [
        entry for entry in entries if entry["classification"] != "untracked"
    ]
    for entry in ordered_entries:
        relative = unb64(entry["path_b64"])
        source = os.path.join(files, relative)
        if not os.path.lexists(source):
            print(f"preserved deletion remains recorded at {os.fsdecode(snapshot)}")
            continue
        target = os.path.join(checkout, relative)
        if entry["classification"] == "untracked":
            if os.path.lexists(target):
                release_copy = unique_sidecar(target, b".from-" + short_sha)
                os.rename(target, release_copy)
                restored.append(release_copy)
                print(
                    f"incoming {os.fsdecode(relative)} retained as "
                    f"{os.fsdecode(os.path.relpath(release_copy, checkout))}"
                )
            safe_parent(checkout, relative)
            copy_node(source, target)
            restored.append(target)
            print(f"restored untracked operator path {os.fsdecode(relative)}")
        else:
            local_copy = unique_sidecar(target, b".local")
            safe_parent(checkout, os.path.relpath(local_copy, checkout))
            copy_node(source, local_copy)
            restored.append(local_copy)
            print(
                f"release kept {os.fsdecode(relative)}; operator copy is "
                f"{os.fsdecode(os.path.relpath(local_copy, checkout))}"
            )
    for path in restored:
        fsync_output(path, checkout)


def prune_command(args):
    root = os.fsencode(args[0])
    keep = int(args[1])
    label = args[2] if len(args) > 2 else None
    candidates = []
    for name in os.listdir(root):
        if name.startswith(b"."):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        try:
            metadata = verify_snapshot(path)
        except (OSError, ValueError, PreserveError):
            continue
        if label is None or metadata.get("label") == label:
            candidates.append((name, path))
    candidates.sort(reverse=True)
    for _, path in candidates[keep:]:
        tombstone = path + b".pruning-" + str(os.getpid()).encode("ascii")
        os.rename(path, tombstone)
        fsync_dir(root)
        shutil.rmtree(tombstone)
        fsync_dir(root)


def main():
    if len(sys.argv) < 2:
        raise PreserveError("missing preservation helper command")
    command, args = sys.argv[1], sys.argv[2:]
    if command == "snapshot":
        snapshot_command(args)
    elif command == "prepare":
        prepare_command(args)
    elif command == "restore":
        restore_command(args)
    elif command == "verify":
        verify_snapshot(os.fsencode(args[0]))
    elif command == "prune":
        prune_command(args)
    elif command == "fsync-tree":
        fsync_tree(os.fsencode(args[0]))
    else:
        raise PreserveError(f"unknown preservation helper command: {command}")


try:
    main()
except (OSError, ValueError, PreserveError) as error:
    print(f"preservation failed: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
}

preserve_checkout() {
    local dir="$1" label="$2" phase="${3:-forward}" root output
    PLEB_PRESERVE_RESULT=""
    git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
    _pleb_assert_preservation_root_outside_checkouts
    root="$(_pleb_preservation_root)"
    _pleb_private_data_dir "$root"
    output="$(_pleb_preserve_python snapshot "$dir" "$root" "$label" "$phase" \
        "$_PLEB_PRESERVE_MAX_BYTES" "$_PLEB_PRESERVE_FREE_RESERVE" \
        "$_PLEB_PRESERVE_KEEP")" || return 1
    # shellcheck disable=SC2034 # public result channel for update.sh callers
    PLEB_PRESERVE_RESULT="$output"
    if [ -n "$output" ]; then
        log "preserved $label checkout changes at $output"
    fi
}

prepare_preserved_checkout() {
    local dir="$1" snapshot="$2"
    [ -n "$snapshot" ] || return 0
    _pleb_preserve_python prepare "$dir" "$snapshot"
}

restore_preserved_checkout() {
    local dir="$1" snapshot="$2" mode="$3" target_sha="${4:-unknown}"
    [ -n "$snapshot" ] || return 0
    _pleb_preserve_python restore "$dir" "$snapshot" "$mode" "$target_sha"
}

verify_preserved_checkout() {
    _pleb_preserve_python verify "$1"
}

fsync_update_record() {
    _pleb_preserve_python fsync-tree "$1"
}
