#!/usr/bin/env python3

import argparse

from encodium import Encodium, Integer

from cryptonet import Cryptonet
from cryptonet.miner import Miner
from cryptonet.utilities import global_hash
from cryptonet.errors import ValidationError
from cryptonet.debug import debug, print_traceback, enable_debug
from cryptonet.statemaker import StateMaker
from cryptonet.dapp import Dapp
from cryptonet.datastructs import MerkleLeavesToRoot
from cryptonet.rpcserver import RPCServer


parser = argparse.ArgumentParser()
parser.add_argument('-port', type=int, help='port to run p2p client on', default=32000)
parser.add_argument('-rpc-port', type=int, help='port to run RCP client on', default=32550)
parser.add_argument('-mine', action='store_true', help='include this flag to mine')
args = parser.parse_args()


class MinBlockWithState(Encodium):
    ''' Minimum specification needed for functional Chain.
    See cryptonet.skeleton for unencumbered examples.
    '''
    parent_hash = Integer.Definition(length=32)
    height = Integer.Definition(default=0)
    nonce = Integer.Definition(default=0)
    state_root = Integer.Definition(length=32, default=0)
    tx_root = Integer.Definition(length=32, default=0)

    def __init__(self, *args, **kwargs):
        super().__init__(self, *args, **kwargs)
        self.priority = self.height
        self.state_maker = None
        self.super_state = None
        self.super_txs = []
        
    def __hash__(self):
        return self.get_hash()
    
    def assert_internal_consistency(self):
        self.assert_true(0 <= self.parent_hash < 2**256, 'Parent hash in valid range')
        self.assert_true(0 <= self.nonce < 256**4, 'Nonce within valid range')
        self.assert_true(self.valid_proof(), 'PoW must be okay (but not yet fully confirmed)')
    
    def assert_validity(self, chain):
        self.assert_internal_consistency()
        if chain.initialized:
            self._set_state_maker(chain.head.state_maker)
            self.assert_true(chain.has_block_hash(self.parent_hash), 'Parent unknown')
            self.assert_true(chain.get_block(self.parent_hash).height + 1 == self.height, 'Height requirement')
            self.assert_true(self.super_state.get_hash() == self.state_root, 'State root must match expected')
        else:
            self.assert_true(self.height == 0, 'Genesis req.: height must be 0')
            self.assert_true(self.parent_hash == 0, 'Genesis req.: parent_hash must be zeroed')
        
    def to_bytes(self):
        return self.to_bencode()
        
    def get_hash(self):
        return global_hash(self.to_bytes())
        
    def get_candidate(self, chain):
        # todo : fix so state_root matches expected
        return self.state_maker.future_block

    def get_pre_candidate(self, chain):
        # fill in basic info here, state_root and tx_root will come later
        candidate = chain._Block(parent_hash=self.get_hash(), height=self.height+1)
        return candidate

    def increment_nonce(self):
        self.nonce += 1

    def valid_proof(self):
        return self.get_hash() < 0x0002000000000000000000000000000000000000000000000000000000000000
        
    def better_than(self, other):
        if other is None:
            return True
        return self.height > other.height

    def related_blocks(self):
        return [(self.height - 1, self.parent_hash)]

    def reorganisation(self, chain, from_block, around_block, to_block, is_test=False):
        ''' self.reorganisation() should be called only on the current head, where to_block is
        to become the new head of the chain.

                 #3--#4--
        -#1--#2<     ^-----from
                 #3a-#4a-#5a-
              ^-- around  ^---to

        If #4 is the head, and #5a arrives, all else being equal, the following will be called:
        from_block = #4
        around_block = #2
        to_block = #5a


        Steps:
        10. From around_block find the prune point
        20. Get prune level from the StateMaker (Will be lower or equal to the LCA in terms of depth).
        30. Prune to that point.
        40. Re-evaluate state from that point to new head.

        if is_test == True then no permanent changes are made.
        '''
        assert self.state_maker != None
        success = self.state_maker.reorganisation(chain, from_block, around_block, to_block, is_test)
        if success:
            to_block._set_state_maker(self.state_maker)
        return success

    def assert_true(self, condition, message):
        if not condition:
            raise ValidationError('Block Failed Validation: %s' % message)

    def on_genesis(self, chain):
        assert not chain.initialized
        self.state_maker = StateMaker(chain)
        self.super_state = self.state_maker.super_state
        debug('Block.on_genesis called')

        class Counter(Dapp):

            def on_block(self, block, chain):
                if block.height > 0:
                    last_value = self.state[block.height - 1]
                    if last_value > 1:
                        if last_value % 2 == 0:
                            self.state[block.height] = last_value // 2
                        else:
                            self.state[block.height] = 3 * last_value + 1
                    else:
                        self.state[block.height] = block.height
                    debug('Counter: on_block called.', self.state.key_value_store)

            def on_transaction(self, subtx, block, chain):
                pass

        self.state_maker.register_dapp(Counter(b'', self.state_maker))

        self.setup_rpc()

    def _set_state_maker(self, state_maker):
        self.state_maker = state_maker
        self.super_state = state_maker.super_state

    def update_roots(self):
        self.state_root = self.state_maker.super_state.get_hash()
        self.tx_root = MerkleLeavesToRoot(leaves=self.super_txs).get_hash()

    def setup_rpc(self):
        self.rpc = RPCServer(port=args.rpc_port)

        @self.rpc.add_method
        def getinfo(*args):
            state = self.state_maker.super_state[b'']
            keys = state.all_keys()
            return {
                "balance": max(keys),
                "height": max(keys),
            }

        self.rpc.run()

    @classmethod
    def get_unmined_genesis(cls):
        return cls(parent_hash=0, height=0, nonce=0, state_root=0, tx_root=0)

min_net = Cryptonet([('127.0.0.1', 32000)], ('127.0.0.1', args.port), block_class=MinBlockWithState, mine=args.mine)

enable_debug()

def make_genesis():
    # genesis_block = MinBlockWithState.make(parent_hash=0,height=0)
    # genesis_block._set_state_maker(StateMaker(min_net.chain, MinBlockWithState))
    # genesis_block.update_roots()
    # miner = Miner(min_net.chain, min_net.seek_n_build)
    # miner.mine(genesis_block)
    # debug('Genesis Block: ', genesis_block.serialize())
    pass

if __name__ == "__main__":
    min_net.run()
