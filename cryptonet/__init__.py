import time

from spore import Spore

from cryptonet.seeknbuild import SeekNBuild
from cryptonet.chain import Chain
from cryptonet.utilities import global_hash
from cryptonet.database import Database
from cryptonet.errors import ValidationError
from cryptonet.datastructs import Intro, BytesList, HashList
from cryptonet.miner import Miner
from cryptonet.debug import debug
import cryptonet.standard

config = {'network_debug': True}

# TODO: STATE
# TODO: Alerts

class FakeSpore(object):
    def run(self):
        while True:
            time.sleep(0.01)
    def shutdown(self):
        pass
    def broadcast(self, *args, **kwargs):
        pass

class Cryptonet(object):
    def __init__(self, seeds, address, block_class=cryptonet.standard.Block, mine=False, alert_pubkey_x=0, enable_p2p=True):
        if enable_p2p:
            self.p2p = Spore(seeds=seeds, address=address)
            self.set_handlers()
        else:
            self.p2p = FakeSpore()

        self.db = Database()
        self.chain = Chain(db=self.db)
        self.seek_n_build = SeekNBuild(self.p2p, self.chain)
        self.mine = mine
        self.miner = Miner(self.chain, self.seek_n_build)

        self.mine_genesis = False
        if not hasattr(block_class, 'GENESIS') or block_class.GENESIS is None:
            self.mine_genesis = True
        else:
            self.genesis = block_class.GENESIS

        self.intros = {}

        self.alert_pubkey_x = alert_pubkey_x
        self.alerts = {}

        self._Block = block_class
        self.chain._Block = block_class
        if self.mine_genesis:
            self.genesis = self._Block.get_unmined_genesis()
            self.miner.mine(provided_block=self.genesis)

        self.chain.set_genesis(self.genesis)

    def run(self):
        if self.mine: self.miner.run()
        self.p2p.run()
        self.seek_n_build.shutdown()
        if self.mine: self.miner.shutdown()

    def shutdown(self):
        self.p2p.shutdown()

    # =================
    # Decorators
    #=================

    def block(self, block_object):
        self._Block = block_object
        self.chain._Block = block_object
        if self.mine_genesis:
            genesis_block = self._Block.get_unmined_genesis()
            self.miner.mine(genesis_block)
        else:
            genesis_block = self._Block().make(self.genesis_binary)
        self.chain.set_genesis(genesis_block)
        return block_object


    #==================
    # Cryptonet Handlers
    #==================

    def set_handlers(self):
        debug('set_handlers')

        @self.p2p.on_connect
        def on_connect_handler(node):
            debug('on_connect_handler')
            my_intro = Intro(top_block=self.chain.head.get_hash())
            node.send(b'intro', my_intro)


        @self.p2p.on_message(b'intro', Intro.deserialize)
        def intro_handler(node, their_intro):
            debug('intro_handler')
            if config['network_debug'] or True:
                debug('MSG intro : %064x' % their_intro.get_hash())
            self.intros[node] = their_intro
            debug('intro_handler: the peer: ', node.address)
            if their_intro.top_block != 0 and not self.chain.has_block_hash(their_intro.top_block):
                debug('intro_handler: their top_block %064x' % their_intro.top_block)
                self.seek_n_build.seek_hash_now(their_intro.top_block)


        @self.p2p.on_message(b'blocks', BytesList.deserialize)
        def blocks_handler(node, block_list):
            if config['network_debug'] or True:
                debug('MSG blocks : %064x' % block_list.get_hash())
            for serialized_block in block_list:
                try:
                    potential_block = self._Block.deserialize(serialized_block)
                    potential_block.assert_internal_consistency()
                    debug('blocks_handler: accepting block of height %d' % potential_block.height)
                except ValidationError as e:
                    debug('blocks_handler: serialized_block:', serialized_block)
                    debug('blocks_handler error', e)
                    #node.misbehaving()
                    continue
                self.seek_n_build.add_block(potential_block)
                self.seek_n_build.seek_many_with_priority(potential_block.related_blocks())


        @self.p2p.on_message(b'request_blocks', HashList.deserialize)
        def request_blocks_handler(node, requests):
            if config['network_debug'] or True:
                debug('MSG request_blocks : %064x' % requests.get_hash())
            blocks_to_send = BytesList()
            for bh in requests:
                if self.chain.has_block_hash(bh):
                    blocks_to_send.append(self.chain.get_block(bh).serialize())
            if blocks_to_send.len() > 0:
                node.send(b'blocks', blocks_to_send)

                # done setting handlers
