import os
import subprocess
import sys

import psutil
from testfixtures import Replacer, compare

from chimera.processes import (
    process_ancestry,
    process_create_time,
    psutil_parent_info,
    same_process,
)


class TestProcessAncestry:
    def test_walks_up_to_a_root_process(self, replace: Replacer) -> None:
        parents = {100: (10, 'sh'), 10: (1, 'claude')}
        replace.in_module(psutil_parent_info, lambda pid: parents[pid])
        compare(
            process_ancestry(100),
            expected=[{'pid': 10, 'name': 'sh'}, {'pid': 1, 'name': 'claude'}],
        )

    def test_stops_once_a_process_is_unqueryable(self, replace: Replacer) -> None:
        parents = {100: (10, 'sh')}
        replace.in_module(psutil_parent_info, lambda pid: parents.get(pid))
        compare(process_ancestry(100), expected=[{'pid': 10, 'name': 'sh'}])

    def test_bounds_a_pathological_chain(self, replace: Replacer) -> None:
        replace.in_module(psutil_parent_info, lambda pid: (pid + 1, 'x'))
        compare(
            process_ancestry(100),
            expected=[{'pid': n, 'name': 'x'} for n in range(101, 121)],
        )

    def test_walks_a_real_child_process(self) -> None:
        # no mocking: proves the loop and psutil_parent_info together resolve a
        # genuine parent/child relationship, not just the stubbed shapes above
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']) as child:
            try:
                ancestry = process_ancestry(child.pid)
            finally:
                child.terminate()
        assert ancestry
        compare(ancestry[0]['pid'], expected=os.getpid())
        assert ancestry[0]['name']


class TestPsutilParentInfo:
    def test_returns_a_real_childs_parent_pid_and_name(self) -> None:
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']) as child:
            try:
                info = psutil_parent_info(child.pid)
            finally:
                child.terminate()
        assert info is not None
        parent_pid, name = info
        compare(parent_pid, expected=os.getpid())
        assert name

    def test_none_when_the_process_is_really_gone(self) -> None:
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait()  # reaped: the pid no longer names a process
        compare(psutil_parent_info(child.pid), expected=None)

    def test_none_when_at_the_root_of_the_tree(self, replace: Replacer) -> None:
        # a real, live Process (our own) with only .parent() faked, rather than a
        # hand-rolled stub class standing in for the whole of psutil.Process
        replace.on_class(psutil.Process.parent, lambda _self: None)
        compare(psutil_parent_info(os.getpid()), expected=None)

    def test_none_when_access_is_denied(self, replace: Replacer) -> None:
        def deny(self: psutil.Process) -> None:
            raise psutil.AccessDenied(self.pid)

        replace.on_class(psutil.Process.parent, deny)
        compare(psutil_parent_info(os.getpid()), expected=None)


class TestProcessCreateTime:
    def test_reads_a_real_processes_creation_time(self) -> None:
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']) as child:
            try:
                created = process_create_time(child.pid)
            finally:
                child.terminate()
        assert created is not None
        assert created > 0

    def test_stable_across_reads_of_the_same_process(self) -> None:
        # the whole point: the pair (pid, create_time) must identify one process over
        # time, so two reads of the same live pid can never disagree
        compare(process_create_time(os.getpid()), expected=process_create_time(os.getpid()))

    def test_none_when_the_process_is_really_gone(self) -> None:
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait()
        compare(process_create_time(child.pid), expected=None)

    def test_none_when_access_is_denied(self, replace: Replacer) -> None:
        def deny(self: psutil.Process) -> float:
            raise psutil.AccessDenied(self.pid)

        replace.on_class(psutil.Process.create_time, deny)
        compare(process_create_time(os.getpid()), expected=None)


class TestSameProcess:
    def test_identical_creation_times_are_one_process(self) -> None:
        assert same_process(1785347678.542948, 1785347678.542948)

    def test_storage_rounding_still_matches(self) -> None:
        assert same_process(1785347678.5429, 1785347678.542948)

    def test_a_reused_pid_does_not_match(self) -> None:
        assert not same_process(1785347678.542948, 1785347999.1)

    def test_an_unknown_creation_time_is_never_evidence(self) -> None:
        # unreadable (another user's process) must not read as "different process":
        # refusing to act on absent evidence would break every such session
        assert same_process(None, 1785347678.542948)
        assert same_process(1785347678.542948, None)
        assert same_process(None, None)
