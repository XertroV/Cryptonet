import time
import queue
import threading
import asyncio

from cryptonet.datastructs import *
from cryptonet.debug import debug, verbose_debug


class AtomicIncrementor:
    def __init__(self):
        self.lock = threading.Lock()
        self.counter = 0

    def get_next(self):
        with self.lock:
            r = self.counter
            self.counter += 1
        return r


class SeekNBuild:
    ''' The SeekNBuild class is responsible for attempting to acquire all known
    blocks, and facilitate the Chain object finding the longest PoW chain possible.

    See the block_seeker and chain_builder functions for more info.
    '''

    def __init__(self, p2p, chain, loop=None):
        self.p2p = p2p
        self.chain = chain
        self.chain.learn_of_seek_n_build(self)

        self.nonces = AtomicIncrementor()

        self.future = set()
        self.future_queue = asyncio.PriorityQueue()
        self.present = set()
        self.present_queue = asyncio.PriorityQueue()
        self.past = set()
        self.past_queue = asyncio.PriorityQueue()
        self.past_queue_no_parent = asyncio.PriorityQueue()
        self.done = set()
        self.all = set()
        self._shutdown = False

        self._funcs = {
            'height': self.chain.get_height,
        }

        self._loop = asyncio.new_event_loop() if loop is None else loop
        asyncio.async(self.block_seeker(), loop=self._loop)
        asyncio.async(self.block_reseeker(), loop=self._loop)
        asyncio.async(self.chain_builder(), loop=self._loop)

    def max_blocks_at_once(self):
        # no reason for special values besides
        return int(max(5, min(500, self.get_chain_height()) // 3))

    def shutdown(self):
        self._shutdown = True

    def seek_hash_now(self, block_hash):
        ''' Add block_hash to queue with priority -1 (will be pulled next).
        '''
        @asyncio.coroutine
        def do(block_hash):
            if block_hash == 0:
                return
            if block_hash not in self.all:
                self.future_queue.put_nowait((-1, block_hash))
                self.future.add(block_hash)
                self.all.add(block_hash)
        asyncio.async(do(block_hash), loop=self._loop)

    def seek_with_priority(self, block_hash_with_height):
        ''' Add block_hash to future queue with its priority.
        '''
        @asyncio.coroutine
        def do(block_hash_with_height):
            height, block_hash = block_hash_with_height
            if block_hash == 0: return
            if block_hash not in self.all:
                self.all.add(block_hash)
                self.future_queue.put_nowait((height, block_hash))
                self.future.add(block_hash)
        asyncio.async(do(block_hash_with_height), loop=self._loop)

    def seek_many_with_priority(self, block_hashes_with_height):
        ''' Applies each in list to seek_with_priority()
        '''
        for height, block_hash in block_hashes_with_height:
            self.seek_with_priority((height, block_hash))

    @asyncio.coroutine
    def block_reseeker(self):
        '''
        1. Are there any blocks that were requested more than X seconds ago
            1.1 throw it away if it is no longer in the self.present set
            1.2 for each block that was, add it to requesting
        '''
        requesting = IntList()
        while not self._shutdown:
            to_get = min(len(self.future), self.max_blocks_at_once()) - requesting.len()
            oldest_timestamp, oldest_block_hash = yield from self.present_queue.get()
            if oldest_timestamp + 10 < time.time() and to_get > 0:  # requested >10s ago
                if oldest_block_hash in self.present:
                    if self.chain.has_block_hash(oldest_block_hash):
                        self.present.remove(oldest_block_hash)
                    else:
                        debug('seeker, block re-request: %064x' % oldest_block_hash)
                        requesting.append(oldest_block_hash)
            else:
                yield from self.present_queue.put((oldest_timestamp, oldest_block_hash))  # put hash back in the queue
                if len(requesting) > 0:
                    self.p2p.broadcast(b'request_blocks', requesting.serialize())
                    for hash in requesting:
                        self.present_queue.put_nowait((time.time(), hash))
                    requesting = IntList()
                yield from asyncio.sleep(0.5)

    @asyncio.coroutine
    def block_seeker(self):
        ''' block_seeker() is run through asyncio.
        block_seeker will run in a loop and:
        2. Find the number of blocks to send
            2.1 get that many from the future_queue
        3. For each block_hash to request, add it to the present_queue with the time it was requested.
        4. Pick a random peer and send the request to it.
        '''
        while not self._shutdown and not self.chain.initialized:
            yield from asyncio.sleep(0.1)

        while not self._shutdown:
            requesting = IntList()  # encodium object

            to_get = min(len(self.future), self.max_blocks_at_once())
            if to_get > 0:
                # pick some blocks to request
                for i in range(to_get):
                    priority, h = self.future_queue.get_nowait()
                    #print('block_seeker: asking for height: ',_)
                    self.future.remove(h)
                    if priority != 0:  # note: why exclude priority 0? genesis block?
                        requesting.append(h)

            for h in requesting:
                self.present_queue.put_nowait((int(time.time()), h))
                self.present.add(h)

            if requesting.len() > 0:
                # TODO : don't broadcast to all nodes, just one
                debug('Requesting: ', requesting.serialize(), len(self.future))
                self.p2p.broadcast(b'request_blocks', requesting.serialize())
            else:
                yield from asyncio.sleep(0.01)

    def get_chain_height(self):
        return self._funcs['height']()

    def broadcast_block(self, to_send):
        self.p2p.broadcast(b'blocks', to_send.serialize())

    def add_block(self, block):
        '''
        Add a block to the past_queue (ready for chain_builder) if we haven't done so before.
        '''
        # blocks should be internally consistent at this point
        @asyncio.coroutine
        def _add_block(block):
            block_hash = block.get_hash()
            if block_hash in self.past or block_hash in self.done:
                return
            debug('SNB: Add Block %s' % block_hash)

            to_put = (block.height, self.nonces.get_next(), block)
            self.past.add(block_hash)
            self.past_queue.put_nowait(to_put)

            self.all.add(block_hash)

            if block_hash in self.present:
                self.present.remove(block_hash)
        asyncio.async(_add_block(block), loop=self._loop)

    @asyncio.coroutine
    def chain_builder(self):
        '''
        1. Get the next block.
        2. If the block is potentially the next block (or older that the chain head)
            2.1 Check we don't already have it
            2.2 Ensure it's valid
            2.3 Add it to the Chain
            2.4 Broadcast to peers
        '''
        while not self._shutdown and not self.chain.initialized:
            yield from asyncio.sleep(0.1)

        while not self._shutdown:
            height, nonce, block = yield from self.past_queue.get()

            debug('Chain Builder:', height, nonce, block, self.chain.head)

            if block.height == 0:
                self.past.remove(block.get_hash())
                self.done.add(block.get_hash())
                continue

            block_hash = block.get_hash()
            #print('chain_builder: checking %d' % block.height)
            # TODO : handle orphans intelligently
            if block.height > self.get_chain_height() + 1:
                #print('chain_builder: chain height: %d' % self.get_chain_height())
                #print('chain_builder: block.height %d' % block.height)
                # try some of those which were parentless:
                self.past_queue.put_nowait((height, nonce, block))
                while not self.past_queue_no_parent.empty():
                    self.past_queue.put_nowait(self.past_queue_no_parent.get_nowait())
                self.seek_hash_now(block.get_hash())
                yield from asyncio.sleep(0.05)
            else:
                if self.chain.has_block(block_hash):
                    self.past.remove(block_hash)
                    self.done.add(block_hash)
                    continue

                # TODO : handle orphans intelligently
                if not self.chain.has_block_hash(block.parent_hash):
                    debug('chain_builder: don\'t have parent')
                    debug('chain_builder: head and curr', self.chain.head.get_hash(), block.parent_hash)
                    self.past_queue_no_parent.put_nowait((height, nonce, block))
                    continue

                # todo: only broadcast block on success
                self.past.remove(block_hash)
                self.done.add(block_hash)
                self.chain.add_block(block)
                debug('builder to send : %064x' % block.get_hash())
                to_send = BlocksMessage(contents=[block.serialize()])
                debug('builder sending...')
                verbose_debug('builder to send full : %s' % to_send.serialize())
                self.broadcast_block(to_send)
                debug('builder success : %064x' % block.get_hash())
