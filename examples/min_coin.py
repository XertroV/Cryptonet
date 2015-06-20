#!/usr/bin/python3

''' The purpose of this coin is to be the smallest cryptocurrency cryptonet can support.
It uses standard blocks and transactions, as well as TxPrism and TxTracker
'''

import argparse
from binascii import unhexlify

from cryptonet import Cryptonet
from cryptonet.debug import debug, enable_debug

import cryptonet.standard

cryptonet.standard.Block.GENESIS = cryptonet.standard.Block(
    header=cryptonet.standard.Header(
        version=1,
        nonce=37,
        height=0,
        timestamp=1403446730,
        target=2 ** 248,
        sigma_diff=256,
        state_mr=0,
        transaction_mr=0,
        uncles_mr=0,
        previous_blocks=[0]
    )
)

parser = argparse.ArgumentParser()
parser.add_argument('-mine', action='store_true', default=False, help='mine blocks, include flag to engage')
parser.add_argument('-add-nodes', nargs='*', default=[], type=str,
                    help='node to connect to non-exclusively. Format xx.xx.xx.xx:yyyy')
# parser.add_argument('-genesis', nargs=1, default=[GENESIS], type=str, help='bytes of genesis block if needed')
parser.add_argument('-network-debug', action='store_true', default=False)
parser.add_argument('-debug', action='store_true', default=False)
parser.add_argument('-port', nargs=1, default=[32555], type=int, help='port for node to bind to')
parser.add_argument('-rpc-port', nargs=1, default=[12345], type=int, help='port for rpc server to bind to')
args = parser.parse_args()

if args.debug:
    enable_debug()

seeds = [(h, int(p)) for h, p in [i.split(':') for i in args.add_nodes]]

print(args)

min_coin = Cryptonet(mine=args.mine, seeds=seeds, address=('0.0.0.0', args.port[0]), block_class=cryptonet.standard.Block)

rpc = cryptonet.standard.RCPHandler(min_coin, args.rpc_port[0])

min_coin.run()