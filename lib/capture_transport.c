/* Consent-owned video transport. No X11 access or source selection lives here. */
#define _GNU_SOURCE
#include <pipewire/pipewire.h>
#include <spa/param/video/format-utils.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/mman.h>
#include <unistd.h>

struct capture {
    struct pw_thread_loop *loop;
    struct pw_context *context;
    struct pw_core *core;
    struct pw_stream *stream;
    struct spa_hook stream_hook, core_hook;
    uint32_t width, height, stride, size;
    uint64_t sequence;
    int failed;
};

struct storage { void *pixels; size_t size; int fd; };

static void add_buffer(void *data, struct pw_buffer *buffer) {
    struct capture *c = data;
    struct spa_buffer *b = buffer->buffer;
    if (b->n_datas != 1 || !(b->datas[0].type & (1u << SPA_DATA_MemFd))) {
        c->failed = 1; return;
    }
    struct storage *s = calloc(1, sizeof(*s));
    if (!s) { c->failed = 1; return; }
    s->size = c->size;
    s->fd = memfd_create("pleb-capture-frame", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (s->fd < 0 || ftruncate(s->fd, s->size) < 0 ||
            fcntl(s->fd, F_ADD_SEALS, F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL) < 0) goto fail;
    s->pixels = mmap(NULL, s->size, PROT_READ | PROT_WRITE, MAP_SHARED, s->fd, 0);
    if (s->pixels == MAP_FAILED) goto fail;
    buffer->user_data = s;
    struct spa_data *d = &b->datas[0];
    d->type = SPA_DATA_MemFd; d->fd = s->fd; d->data = s->pixels;
    d->mapoffset = 0; d->maxsize = s->size;
    d->flags = SPA_DATA_FLAG_READABLE | SPA_DATA_FLAG_WRITABLE;
    return;
fail:
    if (s->fd >= 0) close(s->fd);
    free(s); c->failed = 1;
}

static void remove_buffer(void *data, struct pw_buffer *buffer) {
    struct storage *s = buffer->user_data;
    if (!s) return;
    munmap(s->pixels, s->size); close(s->fd); free(s);
    buffer->user_data = NULL;
}

static void state_changed(void *data, enum pw_stream_state old,
        enum pw_stream_state state, const char *error) {
    struct capture *c = data;
    if (state == PW_STREAM_STATE_ERROR || state == PW_STREAM_STATE_UNCONNECTED)
        c->failed = 1;
    pw_thread_loop_signal(c->loop, false);
}

static void core_error(void *data, uint32_t id, int seq, int res, const char *message) {
    struct capture *c = data;
    if (id == PW_ID_CORE && res < 0) c->failed = 1;
    pw_thread_loop_signal(c->loop, false);
}

static void format_changed(void *data, uint32_t id, const struct spa_pod *format) {
    struct capture *c = data;
    struct spa_video_info_raw video = {0};
    uint8_t memory[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
    const struct spa_pod *params[3];
    if (id != SPA_PARAM_Format || !format) return;
    if (spa_format_video_raw_parse(format, &video) < 0 ||
            video.format != SPA_VIDEO_FORMAT_BGRx ||
            video.size.width != c->width || video.size.height != c->height) {
        c->failed = 1;
        return;
    }
    /* Pointer-only buffers work with GStreamer but cannot be mapped by
     * WebRTC. Advertise only shareable descriptors, including for the
     * producer side of the graph, instead of relying on sink negotiation. */
    params[0] = spa_pod_builder_add_object(&b, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers,
        SPA_PARAM_BUFFERS_size, SPA_POD_Int(c->size),
        SPA_PARAM_BUFFERS_stride, SPA_POD_Int(c->stride),
        SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(8, 2, 8),
        SPA_PARAM_BUFFERS_dataType, SPA_POD_CHOICE_FLAGS_Int(1 << SPA_DATA_MemFd));
    params[1] = spa_pod_builder_add_object(&b, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
        SPA_PARAM_META_type, SPA_POD_Id(SPA_META_Header),
        SPA_PARAM_META_size, SPA_POD_Int(sizeof(struct spa_meta_header)));
    params[2] = spa_pod_builder_add_object(&b, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
        SPA_PARAM_META_type, SPA_POD_Id(SPA_META_VideoCrop),
        SPA_PARAM_META_size, SPA_POD_Int(sizeof(struct spa_meta_region)));
    if (pw_stream_update_params(c->stream, params, 3) < 0) c->failed = 1;
}

static const struct pw_stream_events stream_events = {
    PW_VERSION_STREAM_EVENTS, .state_changed=state_changed, .param_changed=format_changed,
    .add_buffer=add_buffer, .remove_buffer=remove_buffer,
};
static const struct pw_core_events core_events = {
    PW_VERSION_CORE_EVENTS, .error=core_error,
};

void pleb_capture_close(struct capture *c) {
    if (!c) return;
    if (c->loop) pw_thread_loop_stop(c->loop);
    if (c->stream) pw_stream_destroy(c->stream);
    if (c->core) pw_core_disconnect(c->core);
    if (c->context) pw_context_destroy(c->context);
    if (c->loop) pw_thread_loop_destroy(c->loop);
    free(c);
    pw_deinit();
}

struct capture *pleb_capture_open(const char *name, uint32_t width, uint32_t height) {
    if (!name || strncmp(name, "pleb-capture-", 13) || strlen(name) > 128 ||
            !width || !height || width > 16384 || height > 16384 ||
            (uint64_t)width * height > 67108864) return NULL;
    pw_init(NULL, NULL);
    struct capture *c = calloc(1, sizeof(*c));
    if (!c) { pw_deinit(); return NULL; }
    c->width = width; c->height = height; c->stride = width * 4; c->size = c->stride * height;
    c->loop = pw_thread_loop_new("pleb-capture", NULL);
    if (!c->loop) goto fail;
    c->context = pw_context_new(pw_thread_loop_get_loop(c->loop), NULL, 0);
    if (!c->context || pw_thread_loop_start(c->loop) < 0) goto fail;
    pw_thread_loop_lock(c->loop);
    c->core = pw_context_connect(c->context, NULL, 0);
    if (!c->core) goto fail_locked;
    pw_core_add_listener(c->core, &c->core_hook, &core_events, c);
    c->stream = pw_stream_new(c->core, "Pleb screen sharing", pw_properties_new(
        PW_KEY_NODE_NAME, name, PW_KEY_NODE_DESCRIPTION, "Pleb screen sharing",
        PW_KEY_MEDIA_CLASS, "Video/Source", NULL));
    if (!c->stream) goto fail_locked;
    pw_stream_add_listener(c->stream, &c->stream_hook, &stream_events, c);
    uint8_t memory[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
    const struct spa_pod *format = spa_pod_builder_add_object(&b, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRx),
        SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(width, height)),
        SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&SPA_FRACTION(30, 1)));
    if (pw_stream_connect(c->stream, PW_DIRECTION_OUTPUT, PW_ID_ANY,
            PW_STREAM_FLAG_DRIVER | PW_STREAM_FLAG_ALLOC_BUFFERS, &format, 1) < 0) goto fail_locked;
    struct timespec deadline;
    pw_thread_loop_get_time(c->loop, &deadline, 5 * SPA_NSEC_PER_SEC);
    while (!c->failed && pw_stream_get_state(c->stream, NULL) < PW_STREAM_STATE_PAUSED) {
        if (pw_thread_loop_timed_wait_full(c->loop, &deadline) < 0) goto fail_locked;
    }
    if (c->failed) goto fail_locked;
    pw_thread_loop_unlock(c->loop);
    return c;
fail_locked:
    pw_thread_loop_unlock(c->loop);
fail:
    pleb_capture_close(c);
    return NULL;
}

/* 1 = delivered, 0 = no consumer/free buffer yet, -1 = failed transport.
 * Never wait for a slow consumer or queue another copy of its pixels. */
int pleb_capture_push(struct capture *c, const void *pixels, size_t size) {
    if (!c || !pixels || size != c->size) return -1;
    pw_thread_loop_lock(c->loop);
    if (c->failed) { pw_thread_loop_unlock(c->loop); return -1; }
    struct pw_buffer *buffer = pw_stream_dequeue_buffer(c->stream);
    if (!buffer) { pw_thread_loop_unlock(c->loop); return 0; }
    struct spa_buffer *b = buffer->buffer;
    if (b->n_datas != 1 || !b->datas[0].data || b->datas[0].maxsize < c->size) {
        pw_stream_queue_buffer(c->stream, buffer);
        c->failed = 1; pw_thread_loop_unlock(c->loop); return -1;
    }
    struct spa_data *d = &b->datas[0];
    memcpy(d->data, pixels, c->size);
    d->chunk->offset = 0; d->chunk->size = c->size; d->chunk->stride = c->stride; d->chunk->flags = 0;
    buffer->size = (uint64_t)c->width * c->height;
    struct spa_meta_header *header = spa_buffer_find_meta_data(b, SPA_META_Header, sizeof(*header));
    if (header) {
        struct timespec now; clock_gettime(CLOCK_MONOTONIC, &now);
        *header = (struct spa_meta_header){.seq=c->sequence++, .pts=(int64_t)now.tv_sec * SPA_NSEC_PER_SEC + now.tv_nsec};
    }
    struct spa_meta_region *crop = spa_buffer_find_meta_data(b, SPA_META_VideoCrop, sizeof(*crop));
    if (crop) crop->region = SPA_REGION(0, 0, c->width, c->height);
    int result = pw_stream_queue_buffer(c->stream, buffer);
    if (result >= 0 && pw_stream_is_driving(c->stream)) result = pw_stream_trigger_process(c->stream);
    if (result < 0) c->failed = 1;
    pw_thread_loop_unlock(c->loop);
    return result < 0 ? -1 : 1;
}
