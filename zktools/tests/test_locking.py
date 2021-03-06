import threading

from nose.tools import eq_
import zookeeper

from zktools.tests import TestBase


class TestAsyncLocking(TestBase):
    def makeOne(self, *args, **kwargs):
        from zktools.locking import ZkAsyncLock
        return ZkAsyncLock(self.conn, *args, **kwargs)

    def setUp(self):
        if self.conn.exists('/ZktoolsLocks/zkALockTest'):
            self.conn.delete_recursive(
                '/ZktoolsLocks/zkALockTest', force=True)

    def test_retryable(self):
        from zktools.locking import retryable
        eq_(True, retryable(zookeeper.CONNECTIONLOSS))

    def testBasicLock(self):
        lock = self.makeOne('zkALockTest')
        lock.acquire()
        lock.wait_for_acquire()
        eq_(True, lock.acquired)
        lock.release()
        lock.wait_for_release()
        eq_(False, lock.acquired)

    def test_with_blocking(self):
        lock = self.makeOne('zkALockTest')
        with lock:
            eq_(True, lock.acquired)
        eq_(False, lock.acquired)

    def test_candidate_release(self):
        lock1 = self.makeOne('zkALockTest')
        lock2 = self.makeOne('zkALockTest')
        lock3 = self.makeOne('zkALockTest')

        vals = []
        pv = threading.Event()
        release_ev = threading.Event()
        released = threading.Event()

        def run():
            lock2.acquire()
            release_ev.wait()
            while not lock2.candidate_created:
                assert vals == []
            vals.append(lock2.acquired)
            released.set()
            lock2.release()

        def waiting():
            with lock3:
                vals.append(2)
                pv.set()

        blocker = threading.Thread(target=run)
        waiter = threading.Thread(target=waiting)
        lock1.acquire()
        lock1.wait_for_acquire()
        blocker.start()
        waiter.start()
        eq_(vals, [])
        release_ev.set()
        released.wait()
        eq_(vals, [False])
        lock1.release()
        lock1.wait_for_release()
        pv.wait()
        eq_(vals, [False, 2])
        blocker.join()
        waiter.join()


class TestLocking(TestBase):
    def makeOne(self, *args, **kwargs):
        from zktools.locking import ZkLock
        return ZkLock(self.conn, *args, **kwargs)

    def setUp(self):
        if self.conn.exists('/ZktoolsLocks/zkLockTest'):
            self.conn.delete_recursive(
                '/ZktoolsLocks/zkLockTest', force=True)

    def testBasicLock(self):
        lock = self.makeOne('zkLockTest')
        lock.clear()
        eq_(bool(lock.acquire()), True)
        eq_(lock.release(), True)

    def testLockRelease(self):
        lock1 = self.makeOne('zkLockTest')
        lock2 = self.makeOne('zkLockTest')

        vals = []
        pv = threading.Event()
        al = threading.Event()

        def run():
            pv.set()
            with lock2:
                vals.append(2)
                al.set()
        waiter = threading.Thread(target=run)
        lock1.acquire()
        waiter.start()
        pv.wait()
        eq_(vals, [])
        lock1.release()
        waiter.join()
        al.wait()
        eq_(vals, [2])

    def testLockRevoked(self):
        lock1 = self.makeOne('zkLockTest')
        lock2 = self.makeOne('zkLockTest')

        vals = []
        ev = threading.Event()

        def run():
            with lock2:
                vals.append(2)
                ev.set()
                val = 0
                while not lock2.revoked:
                    val += 1
                ev.set()

        waiter = threading.Thread(target=run)
        waiter.start()
        ev.wait()
        eq_(vals, [2])
        ev.clear()
        lock1.revoke_all()
        ev.wait()
        waiter.join()
        with lock1:
            vals.append(3)
        eq_(vals, [2, 3])


class TestSharedLocks(TestLocking):
    def makeWriteLock(self, *args, **kwargs):
        from zktools.locking import ZkWriteLock
        return ZkWriteLock(self.conn, *args, **kwargs)

    def makeReadLock(self, *args, **kwargs):
        from zktools.locking import ZkReadLock
        return ZkReadLock(self.conn, *args, **kwargs)

    def testLockQueue(self):
        r1 = self.makeReadLock('zkLockTest')
        r2 = self.makeReadLock('zkLockTest')
        w1 = self.makeWriteLock('zkLockTest')

        vals = []

        cv = threading.Event()
        wv = threading.Event()

        def reader():
            with r2:
                cv.set()
                vals.append('r')

        def writer():
            with w1:
                vals.append('w')
            wv.set()

        read2 = threading.Thread(target=reader)
        write1 = threading.Thread(target=writer)
        r1.acquire()
        eq_(r1.has_lock(), True)
        read2.start()
        # Make sure read2 starts before the write1
        cv.wait()
        write1.start()
        read2.join()
        eq_(vals, ['r'])
        r1.release()
        write1.join()
        wv.wait()
        eq_(vals, ['r', 'w'])

    def testRevoked(self):
        from zktools.locking import IMMEDIATE
        w1 = self.makeReadLock('zkLockTest')
        r1 = self.makeWriteLock('zkLockTest')
        ev = threading.Event()
        wv = threading.Event()
        vals = []

        def reader():
            with r1:
                vals.append(1)
                ev.set()
                val = 0
                while not r1.revoked:
                    val += 1

        def writer():
            with w1(revoke=IMMEDIATE):
                vals.append(2)
                wv.set()

        reader = threading.Thread(target=reader)
        writer = threading.Thread(target=writer)
        reader.start()
        ev.wait()
        eq_(vals, [1])
        writer.start()
        reader.join()
        writer.join()
        wv.wait()
        eq_(vals, [1, 2])

    def testGentleRevoke(self):
        w1 = self.makeReadLock('zkLockTest')
        r1 = self.makeWriteLock('zkLockTest')
        ev = threading.Event()
        wv = threading.Event()
        vals = []

        def reader():
            with r1:
                vals.append(1)
                ev.set()
                val = 0
                while not r1.revoked:
                    val += 1

        def writer():
            with w1(revoke=True):
                vals.append(2)
                wv.set()

        reader = threading.Thread(target=reader)
        writer = threading.Thread(target=writer)
        reader.start()
        ev.wait()
        eq_(vals, [1])
        writer.start()
        reader.join()
        writer.join()
        wv.wait()
        eq_(vals, [1, 2])

    def testTimeOut(self):
        w1 = self.makeReadLock('zkLockTest')
        r1 = self.makeWriteLock('zkLockTest')

        vals = []
        ev = threading.Event()
        wv = threading.Event()

        def reader():
            with r1:
                vals.append(1)
                ev.set()
                val = 0
                while not r1.revoked:
                    val += 1

        def writer():
            result = w1.acquire(timeout=0)
            if result:  # pragma: nocover
                vals.append(2)
            vals.append(3)
            with w1(revoke=True):
                vals.append(4)
                wv.set()

        reader = threading.Thread(target=reader)
        writer = threading.Thread(target=writer)
        reader.start()
        ev.wait()
        eq_(vals, [1])
        writer.start()
        reader.join()
        writer.join()
        wv.wait()
        eq_(vals, [1, 3, 4])

    def testClearing(self):
        w1 = self.makeReadLock('zkLockTest')
        r1 = self.makeWriteLock('zkLockTest')

        vals = []
        ev = threading.Event()

        def readera():
            with r1:
                vals.append(1)
                ev.set()
                val = 0
                while not r1.revoked:
                    val += 1

        reader = threading.Thread(target=readera)
        reader.start()
        ev.wait()
        eq_(vals, [1])
        w1.clear()
        reader.join()
        eq_(vals, [1])
