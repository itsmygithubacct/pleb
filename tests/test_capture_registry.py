import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import capture_registry as registry
import capture_sources as sources


class PaneRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'; self.runtime.mkdir(mode=0o700)
        self.directory = self.runtime / 'kilix-capture-sources'; self.directory.mkdir(mode=0o700)
        self.proc = self.root / 'proc'; self.proc.mkdir()
        patch = mock.patch.object(registry, '_PROC', self.proc)
        patch.start(); self.addCleanup(patch.stop)
        self.env = {'XDG_RUNTIME_DIR': str(self.runtime), 'DISPLAY': ':0'}
        self.token = 'a' * 32
        self.authority = self.root / 'authority'; self.authority.write_bytes(b'private X cookie'); self.authority.chmod(0o600)
        executable = self.root / 'Xvfb'; executable.touch()
        for pid, parent, tick in ((100, 1, 10), (101, 100, 11), (102, 100, 12)):
            directory = self.proc / str(pid); directory.mkdir()
            self.identity(pid, parent, tick)
        (self.proc/'101/exe').symlink_to(executable)
        self.command([str(executable), ':91', '-screen', '0', '640x480x24', '-auth', str(self.authority), '-nolisten', 'tcp'])
        info = self.authority.stat()
        self.record = {'version': 1, 'id': self.token, 'label': 'Test app', 'owner_pid': 100, 'owner_start': '10',
                       'server_pid': 101, 'server_start': '11', 'app_pid': 102, 'app_start': '12',
                       'display': ':91', 'desktop_display': ':0', 'authority': str(self.authority),
                       'xid': 10, 'width': 640, 'height': 480,
                       'authority_device': info.st_dev, 'authority_inode': info.st_ino}
        self.path = self.directory/(self.token+'.json'); self.write()

    def identity(self, pid, parent, tick, state='S'):
        (self.proc/str(pid)/'stat').write_text(str(pid)+' (process) '+' '.join([state,str(parent)]+['0']*17+[str(tick)]))

    def command(self, args):
        (self.proc/'101/cmdline').write_bytes(b'\0'.join(os.fsencode(arg) for arg in args)+b'\0')

    def write(self):
        self.path.write_text(json.dumps(self.record)); self.path.chmod(0o600)

    def test_live_same_user_local_pane_is_available_without_changing_context(self):
        before = self.env.copy()
        self.assertEqual(registry.read(self.token, self.env), self.record)
        self.assertEqual(registry.records(self.env), [self.record])
        self.assertEqual(self.env, before)

    def test_process_reuse_reparenting_and_exit_remove_the_source(self):
        for pid, parent, tick, state in ((100,1,99,'S'), (101,100,99,'S'), (102,100,99,'S'),
                                         (101,1,11,'S'), (102,1,12,'S'), (100,1,10,'Z'), (102,100,12,'Z')):
            with self.subTest(pid=pid, state=state, parent=parent):
                self.identity(pid,parent,tick,state)
                self.assertIsNone(registry.read(self.token,self.env))
                for p,par,t in ((100,1,10),(101,100,11),(102,100,12)):self.identity(p,par,t)

    def test_authority_replacement_and_permissive_records_are_refused(self):
        self.path.chmod(0o644)
        self.assertIsNone(registry.read(self.token,self.env)); self.path.chmod(0o600)
        self.authority.chmod(0o644)
        self.assertIsNone(registry.read(self.token,self.env)); self.authority.chmod(0o600)
        replacement=self.root/'replacement';replacement.write_bytes(b'other cookie');replacement.chmod(0o600)
        replacement.replace(self.authority)
        self.assertIsNone(registry.read(self.token,self.env))

    def test_symlinks_fifos_and_shared_runtime_are_not_read(self):
        self.path.unlink();self.path.symlink_to(self.authority)
        self.assertIsNone(registry.read(self.token,self.env))
        self.path.unlink();os.mkfifo(self.path,0o600)
        self.assertIsNone(registry.read(self.token,self.env))
        self.path.unlink();self.write();self.runtime.chmod(0o755)
        self.assertIsNone(registry.read(self.token,self.env))

    def test_malformed_duplicate_and_unbounded_records_are_ignored(self):
        for text in ('{', '[]', '{"id":"a","id":"b"}', 'x'*8193):
            self.path.write_text(text)
            self.assertIsNone(registry.read(self.token,self.env))
        self.assertIsNone(registry.read('../escape',self.env))

    def test_wrong_display_server_and_authentication_binding_are_refused(self):
        for field, value in (('id','b'*32),('desktop_display',':1'),('display',':0'),('display','host:1'),
                             ('server_pid',True),('owner_start',10),('authority_inode',0)):
            saved=self.record[field];self.record[field]=value;self.write()
            self.assertIsNone(registry.read(self.token,self.env))
            self.record[field]=saved
        self.write()
        for command in (['Xvfb',':92','-auth',str(self.authority),'-nolisten','tcp'],
                        ['Xvfb',':91','-auth','/other','-nolisten','tcp'],
                        ['Xvfb',':91','-auth',str(self.authority)],
                        ['Xvfb',':91','-auth']):
            self.command(command);self.assertIsNone(registry.read(self.token,self.env))
        (self.proc/'101/exe').unlink();(self.proc/'101/exe').symlink_to(self.authority)
        self.assertIsNone(registry.read(self.token,self.env))

    def test_a_pane_sized_below_the_private_framebuffer_is_offered(self):
        # `kilix run` starts Xvfb with a 3840x2160 framebuffer and sizes the
        # screen to its pane through RandR; it registers that pane size. In
        # the RC5 VM the consent picker never listed such application panes.
        self.command([str(self.proc/'101/exe'), ':91', '-screen', '0', '3840x2160x24',
                      '-auth', str(self.authority), '-nolisten', 'tcp'])
        self.assertEqual(registry.read(self.token, self.env), self.record)
        for screen in ('320x480x24', '640x240x24', '640x480x16', '640x480', 'x480x24'):
            with self.subTest(screen=screen):
                self.command([str(self.proc/'101/exe'), ':91', '-screen', '0', screen,
                              '-auth', str(self.authority), '-nolisten', 'tcp'])
                self.assertIsNone(registry.read(self.token, self.env))

    def test_pane_revalidation_never_falls_back_to_a_physical_display(self):
        selected=sources.Source('pane:'+self.token,'Test app',2,0,0,640,480,10,self.token)
        with mock.patch.object(sources,'_physical_sources') as physical, \
                mock.patch.object(sources,'_pane_source',return_value=selected), \
                mock.patch.object(sources,'physical_environment',return_value=self.env):
            self.assertEqual(sources.revalidate(selected),selected)
            target=sources.source_environment(selected)
            self.assertEqual((target['DISPLAY'],target['XAUTHORITY']),(':91',str(self.authority)))
            self.assertEqual(self.env, {'XDG_RUNTIME_DIR':str(self.runtime),'DISPLAY':':0'})
            self.path.unlink()
            with self.assertRaises(sources.CaptureError):sources.revalidate(selected)
            with self.assertRaises(sources.CaptureError):sources.source_environment(selected)
            physical.assert_not_called()

    def test_changed_private_geometry_requires_fresh_selection(self):
        selected=sources.Source('pane:'+self.token,'Test app',2,0,0,640,480,10,self.token)
        resized=sources.Source(selected.key,selected.label,2,0,0,800,600,10,self.token)
        with mock.patch.object(sources,'physical_environment',return_value=self.env), \
                mock.patch.object(sources,'_pane_source',return_value=resized), self.assertRaises(sources.CaptureError):
            sources.revalidate(selected)

    def test_private_geometry_queries_are_bounded_and_bound_to_the_selected_display(self):
        selected=sources.Source('pane:'+self.token,'Test app',2,0,0,640,480,10,self.token)
        target={'DISPLAY':':91','XAUTHORITY':str(self.authority)}
        good=mock.Mock(returncode=0,stdout='Screen 0: minimum 1 x 1, current 640 x 480, maximum 4096 x 4096\n')
        with mock.patch.object(sources.subprocess,'run',return_value=good) as query:
            sources.verify_capture_geometry(selected,target)
            self.assertEqual(query.call_args.kwargs['env'],target)
            self.assertLessEqual(query.call_args.kwargs['timeout'],.5)
        for result in (mock.Mock(returncode=1,stdout=''),mock.Mock(returncode=0,stdout='Screen 0: current 800 x 600')):
            with mock.patch.object(sources.subprocess,'run',return_value=result),self.assertRaises(sources.CaptureError):
                sources.verify_capture_geometry(selected,target)
        with mock.patch.object(sources.subprocess,'run',side_effect=subprocess.TimeoutExpired('query',.5)),self.assertRaises(sources.CaptureError):
            sources.verify_capture_geometry(selected,target)

    def test_private_source_enumeration_never_connects_to_the_private_server(self):
        with mock.patch.object(sources,'physical_environment',return_value=self.env), \
                mock.patch.object(sources,'_physical_sources',return_value=[]) as physical:
            result=sources.enumerate_sources(2)
        self.assertEqual([(s.pane,s.width,s.height,s.xid) for s in result],[(self.token,640,480,10)])
        physical.assert_called_once_with(2,self.env)


if __name__ == '__main__':unittest.main()
