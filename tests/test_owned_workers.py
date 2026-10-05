"""Process cancellation must never signal the host application's group."""

import os
import signal
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from pyforestscan_qgis.core.owned_workers import terminate_process_tree


@unittest.skipIf(os.name == "nt", "POSIX process group semantics")
class PosixWorkerTerminationTests(unittest.TestCase):
    def test_inherited_group_terminates_only_child(self):
        process = Mock(pid=4321)
        process.poll.return_value = None
        with patch("os.getpgid", return_value=1234), patch("os.getpgrp", return_value=1234), patch("os.killpg") as kill_group:
            terminate_process_tree(process)
        kill_group.assert_not_called()
        process.terminate.assert_called_once_with()

    def test_separate_owned_group_receives_signal(self):
        process = Mock(pid=4321)
        process.poll.return_value = None
        with patch("os.getpgid", return_value=4321), patch("os.getpgrp", return_value=1234), patch("os.killpg") as kill_group:
            terminate_process_tree(process)
        kill_group.assert_called_once_with(4321, signal.SIGTERM)
        process.terminate.assert_not_called()

    def test_shared_coordinator_group_is_not_owned_by_child(self):
        process = Mock(pid=4321)
        process.poll.return_value = None
        with patch("os.getpgid", return_value=2345), patch("os.getpgrp", return_value=1234), patch("os.killpg") as kill_group:
            terminate_process_tree(process)
        kill_group.assert_not_called()
        process.terminate.assert_called_once_with()

    def test_exit_during_termination_is_harmless(self):
        process = Mock(pid=4321)
        process.poll.return_value = None
        process.terminate.side_effect = ProcessLookupError
        with patch("os.getpgid", side_effect=ProcessLookupError):
            terminate_process_tree(process)

    def test_real_shared_group_preserves_parent_and_sibling(self):
        # Isolate the probe so a regression fails the test without killing the
        # test runner. Its ordinary children share the probe's process group.
        probe = '''
import subprocess
import sys
from pyforestscan_qgis.core.owned_workers import terminate_process_tree
children = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) for _ in range(2)]
try:
    terminate_process_tree(children[0])
    children[0].wait(timeout=5)
    assert children[1].poll() is None, "Sibling was terminated"
finally:
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
'''
        result = subprocess.run([sys.executable, "-c", probe], start_new_session=True, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
