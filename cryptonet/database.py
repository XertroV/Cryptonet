import leveldb

from bencodepy import encode as to_bencode, decode as from_bencode


def int_length_for_bytes_conversion(i):
    potential_lengths = [32, 64, 256]  # bits
    for l in potential_lengths:
        if i < 2**l:
            return l
    raise Exception('int too long to convert to bytes (max 256bits)')


def _int_to_bytes(i):
    return i.to_bytes(int_length_for_bytes_conversion(i), 'big')


def serialize_value_and_key(f):
    def inner(self, key, value, *args, **kwargs):
        if type(key) is int:
            key = _int_to_bytes(key)
        if hasattr(value, 'serialize'):
            value = value.serialize()
        assert type(value) is bytes
        return f(self, key, value, *args, **kwargs)
    return inner


def int_key_to_bytes(f):
    def inner(self, key, *args, **kwargs):
        if type(key) is int:
            key = _int_to_bytes(key)
        return f(self, key, *args, **kwargs)
    return inner


class Database:
    ''' A key value store for cryptonet '''

    def __init__(self):
        ''' Typically keys will be hashes and values will be bencoded. '''
        self.leveldb = leveldb.LevelDB('./chain.db')

    def key_exists(self, key):
        try:
            self.get_entry(key)
            return True
        except KeyError:
            return False

    @serialize_value_and_key
    def set_entry(self, key, value):
        self.leveldb.Put(key, value)

    def set_list(self, key, list):
        self.set_entry(key, to_bencode(list))

    @int_key_to_bytes
    def get_entry(self, key):
        return bytes(self.leveldb.Get(key))

    def get_list(self, key):
        return from_bencode(self.get_entry(key))

    def del_entry(self, key):
        return self.leveldb.Delete(key)

    def rpush(self, key, val):
        try:
            self.set_list(key, self.get_list(key) + [val])
        except KeyError:
            self.set_list(key, [val])

    def link_ancestor(self, young, old, diff):
        self.rpush(old + diff, young)
        self.rpush(young - diff, old)

    def set_ancestors(self, block):
        s = 0
        bh = block.get_hash()
        cur = block.parent_hash
        if cur == 0: return True  # genesis block
        self.link_ancestor(bh, cur, 2 ** s)
        while self.key_exists(cur - 2 ** s):
            cur = self.get_list(cur - 2 ** s)[0]  # going backwards will always have only one entry
            s += 1
            self.link_ancestor(bh, cur, 2 ** s)
        return True

    def get_ancestors(self, start):
        #print('\ngetAncestors : %s\n' % repr(self.d))
        ret = [start]
        index = 0
        cur = start
        if cur == 0:
            return ret  # genesis block
        #print('\ngetAncestors subtest : %s\n' % repr(cur - 1))
        while self.key_exists(cur - 2 ** index):
            cur = self.get_list(cur - 2 ** index)[0]
            index += 1
            ret.append(cur)
        return ret

    def get_children(self, block_hash):
        ''' block_hash + delta gives all blocks at (height of block_hash) + delta (provided delta is a power of 2)
        '''
        return self.get_entry(block_hash + 1)