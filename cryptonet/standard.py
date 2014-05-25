import time

from encodium import *
#import nacl.signing

import cryptonet
import cryptonet.chain
from cryptonet.utilities import global_hash
from cryptonet.statemaker import StateMaker
from cryptonet.rpcserver import RPCServer
from cryptonet.datastructs import MerkleLeavesToRoot
from cryptonet.dapp import Dapp, TxPrism
from cryptonet.debug import debug

'''
Hierarchy:
BLOCK [
    HEADER
    UNCLES [
        HEADERS...
    ]
    SUPER_TXS [
        TXS...
        SIG
    ]
]

'''

class Signature(Field):

    def fields():
        sig_bytes = Bytes(defualt=b'')
        pubkey = Integer(length=32, default=0)

    def to_bytes(self):
        return self.sig_bytes + self.pubkey.to_bytes(32, 'big')

    def check_valid_signature(self, message):
        ''' Return true if self.sig_bytes is a valid signature for some `message` (in bytes)
        '''
        return True

    def recover_pubkey(self):
        return self.pubkey

    def get_hash(self):
        return global_hash(self.to_bytes())

    def sign(self, message, privkey):
        ''' Should set v,r,s accordingly to
        '''
        def magic_signing_function(m,p):
            return b''
        self.sig_bytes = magic_signing_function(message, privkey)

class Tx(Field):
    
    def fields():
        dapp = Bytes()
        value = Integer(length=8)
        fee = Integer(length=4)
        data = List(Bytes(), default=[])

    def init(self):
        #self.sender = self.recover_pubkey()
        pass
    
    def to_bytes(self):
        return b''.join([
            self.dapp,
            self.value.to_bytes(8, 'big'),
            self.fee.to_bytes(4, 'big'),
            b''.join(self.data)
        ])
    
    def get_hash(self):
        return global_hash(self.to_bytes())

    def assert_internal_consistency(self):
        pass # if we've got this far the tx should be well formed
        
        
class SuperTx(Field):
    
    def fields():
        txs = List(Tx())
        signature = Signature()

    def init(self):
        self.sender = self.signature.pubkey
        for tx in self.txs:
            tx.sender = self.sender
        self.txs_bytes = b''.join([tx.to_bytes() for tx in self.txs])
        
    def to_bytes(self):
        return b''.join([
            b''.join([tx.to_bytes for tx in self.txs]),
            self.signature.to_bytes(),
        ])
        
    def get_hash(self):
        return global_hash(self.to_bytes())

    def assert_internal_consistency(self):
        '''Should ensure signature is valid, nonce is valid, and each tx is valid
        '''
        for tx in self.txs:
            tx.assert_internal_consistency()
        self.signature.check_valid_signature(self.txs_bytes)

        
class Header(Field):

    DEFAULT_TARGET = 2**248-1
    _TARGET1 = 2**256-1
    RETARGET_PERIOD = 16
    BLOCKS_PER_DAY = 1440
    timeint = lambda : int(time.time())
    
    def fields():
        version = Integer(length=2)
        nonce = Integer(length=8) # nonce second to increase work needed for PoW
        height = Integer(length=4)
        timestamp = Integer(length=5)
        target = Integer(length=32, default=0)
        sigma_diff = Integer(length=32, default=0)
        state_mr = Integer(length=32, default=0)
        transaction_mr = Integer(length=32, default=0)
        uncles_mr = Integer(length=32, default=0)
        previous_blocks = List(Integer(length=32))
        
    def to_bytes(self):
        return b''.join([
            self.version.to_bytes(2,'big'),
            self.nonce.to_bytes(8, 'big'),
            self.height.to_bytes(4, 'big'),
            self.timestamp.to_bytes(5, 'big'),
            self.target.to_bytes(32, 'big'),
            self.sigma_diff.to_bytes(32, 'big'),
            self.state_mr.to_bytes(32, 'big'),
            self.transaction_mr.to_bytes(32, 'big'),
            self.uncles_mr.to_bytes(32, 'big'),
            b''.join([i.to_bytes(32, 'big') for i in self.previous_blocks]),
        ])
        
    def get_hash(self):
        return global_hash(self.to_bytes())
    
    def assert_internal_consistency(self):
        # todo: finish
        ''' self.assert_internal_consistency should validate the following:
        * version as expected
        * nonce not silly
        * timestamp not silly
        * target not silly
        * whatever_mr not silly
        * previous_blocks not silly
        
        'not silly' means the data 'looks' right (length, etc) but the information
        is not validated.
        '''
        pass
        
    def assert_validity(self, chain):
        # todo: finish
        ''' self.assert_validity does not validate merkle roots.
        Since the thing generating the merkle roots is stored in the block, a
        block is invlalid if its list of whatever does not produce the correct
        whatever_mr. The header is not invalid, however.
        
        self.assert_validity should validate the following:
        * self.timestamp no more than 15 minutes in the future and >= median of 
            last 100 blocks.
        * self.target is as expected based on past blocks
        * self.previous_blocks exist and are correct
        '''

        if chain.initialized:
            for block_hash in self.previous_blocks:
                self.assert_true(chain.has_block_hash(block_hash), 'previous_blocks required to be known')
            self.assert_true(chain.get_block(self.parent_hash).height + 1 == self.height, 'Height requirement')
        else:
            self.assert_true(self.height == 0, 'Genesis req.: height must be 0')
            self.assert_true(self.previous_blocks == [0], 'Genesis req.: Previous blocks must be zeroed')
            self.assert_true(self.uncle_mr == 0, 'Genesis req.: uncleMR must be zeroed')

    def increment_nonce(self):
        self.nonce += 1

    def get_pre_candidate(self, chain, previous_block):
        new_header = Header.make(
            version = self.version,
            nonce = 0,
            height=self.height + 1,
            timestamp=Header.timeint(),
            previous_blocks=chain.db.get_ancestors(previous_block.get_hash()),
        )
        new_header.target = Header.calc_sigma_diff(new_header, chain, previous_block)
        new_header.sigma_diff = Header.calc_sigma_diff(new_header, previous_block)
        return new_header


    def calc_expected_target(self, chain, previous_block):
        ''' given self, chain, and previous_block, calculate the expected target '''
        if self.previous_blocks[0] == 0: return Header.DEFAULT_TARGET
        if self.height % Header.RETARGET_PERIOD != 0: return previous_block.header.target

        old_ancestor = chain.get_block(self.previous_blocks[(Header.RETARGET_PERIOD-1).bit_length()])
        timedelta = self.timestamp - old_ancestor.header.timestamp
        expected_timedelta = 60 * 60 * 24 * Header.RETARGET_PERIOD // Header.BLOCKS_PER_DAY

        if timedelta < expected_timedelta // 4: timedelta = expected_timedelta // 4
        if timedelta > expected_timedelta * 4: timedelta = expected_timedelta * 4

        new_target = previous_block.header.target * timedelta // expected_timedelta
        debug('New Target Calculated: %064x, height: %d' % (new_target, self.height))
        return new_target

    # need to test
    def calc_sigma_diff(self, previous_block=None):
        ''' given header, calculate the sigma_diff '''
        if self.previous_blocks[0] == 0: prevsigma_diff = 0
        else: prevsigma_diff = previous_block.header.sigma_diff
        return prevsigma_diff + Header.target_to_diff(self.target)

    def target_to_diff(target):
        return Header._TARGET1 // target


class Block(Field):
    
    def fields():
        header = Header()
        uncles = List(Header(), default=[])
        super_txs = List(SuperTx(), default=[])
        
    def init(self):
        self.parent_hash = self.header.previous_blocks[0]
        self.height = self.header.height
        self.state_maker = None
        self.super_state = None

    def __eq__(self, other):
        if isinstance(other, Block) and other.get_hash() == self.get_hash():
            return True
        return False

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
            to_block.set_state_maker(self.state_maker)
        return success
        
    def get_hash(self):
        return self.header.get_hash()

    #def add_super_txs(self, list_of_super_txs):
    #    self.state_maker.add_super_txs(list_of_super_txs)
        
    def assert_internal_consistency(self):
        ''' self.assert_internal_consistency should validate the following:
        * self.header internally consistent
        * self.uncles are all internally consistent
        * self.super_txs all internally consistent
        * self.header.transaction_mr equals merkle root of self.super_txs
        * self.header.uncles_mr equals merkle root of self.uncles
        '''
        self.header.assert_internal_consistency()
        for uncle in self.uncles:
            uncle.assert_internal_consistency()
        for super_tx in self.super_txs:
            super_tx.assert_internal_consistency()
        self.assert_true(self.header.transaction_mr == MerkleLeavesToRoot.make(leaves=[i.get_hash() for i in self.super_txs]).get_hash(), 'TxMR consistency')
        self.assert_true(self.header.uncles_mr == MerkleLeavesToRoot.make(leaves=[i.get_hash() for i in self.uncles]).get_hash(), 'UnclesMR consistency')
        
    def assert_validity(self, chain):
        ''' self.assert_validity should validate the following:
        * self.header.state_mr equals root of self.super_state
        '''
        self.assert_internal_consistency()
        self.header.assert_validity(chain)
        if chain.initialized:
            self.assert_true(chain.has_block_hash(self.parent_hash), 'Parent must be known')
            self.assert_true(chain.get_block(self.parent_hash).height + 1 == self.height, 'Height requirement')
        else:
            self.assert_true(self.height == 0, 'Genesis req.: height must be 0')
            self.assert_true(self.parent_hash == 0, 'Genesis req.: parent_hash must be zeroed')
        self.assert_true(self.super_state.get_hash() == self.header.state_root, 'State root must match expected')

    def better_than(self, other):
        if other == None:
            return True
        return self.header.sigma_diff > other.header.sigma_diff

    def assert_true(self, condition, message):
        if not condition:
            raise ValidationError('Block Failed Validation: %s' % message)

    def get_candidate(self, chain):
        # todo : fix so state_root matches expected
        return self.state_maker.future_block

    def get_pre_candidate(self, chain):
        # fill in basic info here, state_root and tx_root will come later
        # todo : probably shouldn't reference _Block from chain and just use local object
        return chain._Block.make(header=self.header.get_pre_candidate(chain, self), uncles=[], super_txs=[])

    def increment_nonce(self):
        self.header.increment_nonce()

    def valid_proof(self):
        return True

    def on_genesis(self, chain):
        assert not chain.initialized
        self.set_state_maker(StateMaker(chain))
        debug('Block.on_genesis called')

        # TxPrism is standard root dapp - allows for txs to be passed to contracts
        self.state_maker.register_dapp(TxPrism(b'', self.state_maker))

        self.setup_rpc()

    def set_state_maker(self, state_maker):
        self.state_maker = state_maker
        self.super_state = state_maker.super_state

    def update_roots(self):
        self.header.state_mr = self.state_maker.super_state.get_hash()
        self.header.transaction_mr = MerkleLeavesToRoot.make(leaves=self.super_txs).get_hash()

    def setup_rpc(self):
        self.rpc = RPCServer(port=32550)

        @self.rpc.add_method
        def getinfo(*args):
            state = self.state_maker.super_state[b'']
            keys = state.all_keys()
            return {
                "balance": max(keys),
                "height": max(keys),
            }

        @self.rpc.add_method
        def pushtx(super_tx):
            if super_tx.is_valid():
                # when we can broadcast txs, do that
                pass
            else:
                return {}

        self.rpc.run()
