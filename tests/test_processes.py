import psutil
from testfixtures import Replacer, compare

from chimera.processes import process_ancestry, psutil_parent_info


class TestProcessAncestry:
    def test_walks_up_to_a_root_process(self) -> None:
        parents = {100: (10, 'sh'), 10: (1, 'claude')}
        compare(
            process_ancestry(100, get_parent=lambda pid: parents[pid]),
            expected=[{'pid': 10, 'name': 'sh'}, {'pid': 1, 'name': 'claude'}],
        )

    def test_stops_once_a_process_is_unqueryable(self) -> None:
        parents = {100: (10, 'sh')}
        compare(
            process_ancestry(100, get_parent=lambda pid: parents.get(pid)),
            expected=[{'pid': 10, 'name': 'sh'}],
        )

    def test_bounds_a_pathological_chain(self) -> None:
        compare(
            process_ancestry(100, get_parent=lambda pid: (pid + 1, 'x')),
            expected=[{'pid': n, 'name': 'x'} for n in range(101, 121)],
        )


class TestPsutilParentInfo:
    def test_returns_parent_pid_and_name(self, replace: Replacer) -> None:
        class Parent:
            pid = 42

            def name(self) -> str:
                return 'claude'

        class Proc:
            def __init__(self, _pid: int) -> None:
                pass

            def parent(self) -> Parent:
                return Parent()

        replace.in_module(psutil.Process, Proc)
        compare(psutil_parent_info(123), expected=(42, 'claude'))

    def test_none_when_at_the_root_of_the_tree(self, replace: Replacer) -> None:
        class Proc:
            def __init__(self, _pid: int) -> None:
                pass

            def parent(self) -> None:
                return None

        replace.in_module(psutil.Process, Proc)
        compare(psutil_parent_info(123), expected=None)

    def test_none_when_the_process_is_gone(self, replace: Replacer) -> None:
        class Proc:
            def __init__(self, pid: int) -> None:
                raise psutil.NoSuchProcess(pid)

        replace.in_module(psutil.Process, Proc)
        compare(psutil_parent_info(123), expected=None)

    def test_none_when_access_is_denied(self, replace: Replacer) -> None:
        class Proc:
            def __init__(self, _pid: int) -> None:
                pass

            def parent(self) -> None:
                raise psutil.AccessDenied(123)

        replace.in_module(psutil.Process, Proc)
        compare(psutil_parent_info(123), expected=None)
