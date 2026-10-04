import io
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import capture_portal as capture
from capture_sources import Source


class ScreenshotJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.children=[];self.timers=[]
        self.addCleanup(self.stop_children)
        self.source=Source('pane:'+'a'*32,'Test application',2,0,0,32,16,10,'a'*32)
        self.original_popen=subprocess.Popen
        for patch in (mock.patch.object(capture,'revalidate',side_effect=lambda value:value),
                      mock.patch.object(capture,'physical_environment',return_value={'DISPLAY':':0'}),
                      mock.patch.object(capture.GLib,'timeout_add',side_effect=self.timer)):
            patch.start();self.addCleanup(patch.stop)

    def timer(self, delay, callback):
        self.timers.append((delay,callback));return len(self.timers)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:child.kill()
            child.wait(timeout=2)
            child.stdout.close()

    def png(self, width=32, height=16, noisy=False):
        image=(Image.frombytes('RGB',(width,height),random.Random(0).randbytes(width*height*3))
               if noisy else Image.new('RGB',(width,height),(0,128,64)))
        output=io.BytesIO();image.save(output,format='PNG');return output.getvalue()

    def start(self, data, *, status=0, sleep=False):
        path=self.root/'frame.png';path.write_bytes(data)
        def spawn(*args,**kwargs):
            code='from pathlib import Path;import sys,time;sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes());sys.stdout.buffer.flush();'
            code+='time.sleep(30)' if sleep else 'raise SystemExit('+str(status)+')'
            child=self.original_popen([sys.executable,'-c',code,str(path)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            self.children.append(child);return child
        request=SimpleNamespace(done=False,job=None,results=[])
        def finish(code, results=None):
            if request.done:return
            request.done=True;request.results.append((code,results))
            request.job.close()
        request.finish=finish
        with mock.patch.object(capture.subprocess,'Popen',side_effect=spawn):
            job=capture.ScreenshotJob(self.source,request)
        return job,request

    def finish(self, job, request):
        deadline=time.monotonic()+3
        while not request.done and time.monotonic()<deadline:
            job.poll();time.sleep(.01)
        self.assertTrue(request.done)

    def test_complete_png_is_drained_before_success_even_after_the_child_exits(self):
        self.source=Source(self.source.key,self.source.label,2,0,0,1024,512,10,self.source.pane)
        data=self.png(1024,512,noisy=True);self.assertGreater(len(data),1048576)
        job,request=self.start(data)
        with mock.patch.object(capture,'write_screenshot',return_value='file:///private/image.png') as save:
            self.finish(job,request)
        self.assertEqual(request.results[0][0],0)
        self.assertEqual(bytes(save.call_args.args[0]),data)
        self.assertTrue(job.closed)

    def test_malformed_resized_and_failed_images_publish_no_uri(self):
        for data,status in ((b'not a PNG',0),(self.png(16,8),0),(self.png(),1)):
            job,request=self.start(data,status=status)
            with mock.patch.object(capture,'write_screenshot') as save:self.finish(job,request)
            self.assertEqual(request.results,[(2,None)])
            save.assert_not_called()

    def test_pending_capture_returns_control_and_can_be_cancelled(self):
        job,request=self.start(self.png(),sleep=True)
        self.assertTrue(job.poll());self.assertFalse(request.done)
        request.finish(1)
        self.assertTrue(job.closed)
        job.process.wait(timeout=2)
        self.assertFalse(job.poll())
        self.assertEqual(request.results,[(1,None)])

    def test_capture_deadline_fails_without_creating_a_screenshot(self):
        job,request=self.start(self.png(),sleep=True)
        job.deadline=time.monotonic()-1
        with mock.patch.object(capture,'write_screenshot') as save:self.assertFalse(job.poll())
        self.assertEqual(request.results,[(2,None)])
        save.assert_not_called()
        job.process.wait(timeout=2)

    def test_disappeared_source_cannot_publish_an_already_captured_image(self):
        job,request=self.start(self.png())
        with mock.patch.object(capture,'revalidate',side_effect=capture.CaptureError('source closed')), \
                mock.patch.object(capture,'write_screenshot') as save:self.finish(job,request)
        self.assertEqual(request.results,[(2,None)]);save.assert_not_called()

    def test_real_request_retirement_closes_a_pending_job_even_if_client_is_gone(self):
        request=object.__new__(capture.Request)
        request.done=False;request.dialog=None;request.path='/request';request.job=mock.Mock()
        job=request.job
        request.portal=SimpleNamespace(requests={'/request':request})
        request.remove_from_connection=mock.Mock()
        request.success=mock.Mock(side_effect=capture.dbus.exceptions.DBusException('disconnected'))
        request.finish(2);request.finish(2)
        job.close.assert_called_once();self.assertEqual(request.portal.requests,{})


if __name__=='__main__':unittest.main()
