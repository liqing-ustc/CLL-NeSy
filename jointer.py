import perception, syntax, semantics
import numpy as np
from copy import deepcopy
import sys
from func_timeout import func_timeout, FunctionTimedOut
from utils import SYMBOLS, DEVICE
from collections import Counter, namedtuple
from time import time
import torch
import random

Parse = namedtuple('Parse', ['sentence', 'mask', 'head'])

class Node:
    def __init__(self, symbol, smt):
        self.symbol = symbol
        self.smt = smt
        self.children = []
        self._res = None

    def res(self):
        if self._res is not None:
            return self._res

        self._res = self.smt(*[x.res() for x in self.children])
        if self._res is None or self._res > sys.maxsize:
            self._res = None
        return self._res

    def children_res_valid(self):
        for ch in self.children:
            if ch._res is None: 
                return False
        return True

class AST: # Abstract Syntax Tree
    def __init__(self, pt, semantics, sent_probs=None):
        self.pt = pt
        self.semantics = semantics
        self.sent_probs = sent_probs

        mask = [0 if semantics[s].program is None else 1 for s in pt.sentence]
        nodes = [Node(s, semantics[s]) for s in pt.sentence]

        root_idx = pt.head.index(-1)
        self.root_node = nodes[root_idx]

        for node, h, m in zip(nodes, pt.head, mask):
            if m==0 or h == -1:
                continue
            if mask[h] == 0 and h != root_idx: # if the head node is masked and it is not the root, break and set the root_node to None
                self.root_node = None
                break
            nodes[h].children.append(node)
        self.nodes = nodes

        self._res = None
        try:
            # TODO: set a timeout for the execution
            # self._res = func_timeout(timeout=0.01, func=root_node.res)
            if self.root_node is not None:
                self._res = self.root_node.res() 
        except (IndexError, TypeError, ZeroDivisionError, ValueError, RecursionError, FunctionTimedOut) as e:
            # Must be extremely careful about these errors
            # if isinstance(e, FunctionTimedOut):
            #     print(e)
            pass

    def res(self): return self._res

    def res_all(self): return [nd.res() for nd in self.nodes]

    def abduce(self, y, module=None):
        if self._res is not None and self._res == y:
            return self
        
        if module == 'perception':
            et = self.abduce_perception(y)
            if et is not None:
                return et
        # elif module == 'syntax':
        #     et = self.abduce_syntax(y)
        #     if et is not None:
        #         return et
        elif module == 'semantics':
            et = self.abduce_semantics(y)
            if et is not None:
                return et
        
        return None

        
    def abduce_semantics(self, y):
        # abduce over semantics
        # Currently, if the root node's children are valid, we directly change the result to y
        # In future, we can consider to search the execution tree in a top-down manner
        if self.root_node is not None and self.root_node.children_res_valid():
            if self.root_node.smt.solved and self.root_node.smt.arity == 0:
                unsolveds = [smt.idx for smt in self.semantics if not smt.solved]
                if not unsolveds:
                    return None
                root_node_idx = self.pt.head.index(-1)
                root_node_probs = self.sent_probs[root_node_idx]
                sampling_probs = np.array([root_node_probs[i] for i in unsolveds])
                sym = unsolveds[np.argmax(sampling_probs)]
                self.root_node.symbol = sym
                self.pt.sentence[root_node_idx] = sym
            self._res = y
            self.root_node._res = y
            return self
        return None

    def abduce_perception(self, y):
        # abduce over sentence
        sent_pos_list = np.argsort([self.sent_probs[i, s] for i, s in enumerate(self.pt.sentence)])
        for sent_pos in sent_pos_list:
            s_prob = self.sent_probs[sent_pos]
            for sym in np.argsort(s_prob)[::-1]:
                sentence = deepcopy(self.pt.sentence)
                sentence[sent_pos] = sym
                et = AST(Parse(sentence, self.pt.mask, self.pt.head), self.semantics)
                if et.res() is not None and et.res() == y:
                    return et
        return None

    def abduce_syntax(self, y):
        # abduce syntax by rotating the tree w.r.t the root node
        arcs = self.pt.dependencies
        def get_lc(k):
            return sorted([arc[1] for arc in arcs if arc[0] == k and arc[1] < k])

        def get_rc(k):
            return sorted([arc[1] for arc in arcs if arc[0] == k and arc[1] > k], reverse=True)

        for arc in sorted(arcs, key=lambda x: x[2]):
            h, t = arc[:2]
            head = deepcopy(self.pt.head)

            head[t] = head[h]
            head[h] = t 

            children = get_rc(h) if h < t else get_lc(h)
            for j in children[:children.index(t)]:
                head[j] = t

            children = get_lc(t) if h < t else get_rc(h)
            for j in children:
                head[j] = h

            et = AST(Parse(self.pt.sentence, self.pt.mask, head), self.semantics)
            if et.res() is not None and et.res() == y:
                return et


        return None

    
class Jointer:
    def __init__(self, config=None):
        super(Jointer, self).__init__()
        self.config = config
        self.perception = perception.build(config)
        self.syntax = syntax.build(config)
        self.semantics = semantics.build(config)
        self.ASTs = []
        self.buffer = []
        self.epoch = 0
        self.learning_schedule = ['perception'] * (0 if config.perception else 10) \
                               + ['syntax'] * (0 if config.syntax else 5) \
                               + ['semantics'] * (0 if config.semantics else 1)

    @property
    def learned_module(self):
        return self.learning_schedule[self.epoch % len(self.learning_schedule)]

    def save(self, save_path, epoch=None):
        model = {'epoch': epoch}
        model['perception'] = self.perception.save()
        model['syntax'] = self.syntax.save()
        model['semantics'] = self.semantics.save()
        torch.save(model, save_path)

    def load(self, load_path):
        model = torch.load(load_path)
        self.perception.load(model['perception'])
        self.syntax.load(model['syntax'])
        self.semantics.load(model['semantics'])
        return model['epoch']

    def print(self):
        if self.config.perception:
            print('use ground-truth perception.')
        else:
            print(self.perception.model)
        if self.config.syntax:
            print('use ground-truth syntax.')
        else:
            print(self.syntax.model)
        if self.config.semantics:
            print('use ground-truth semantics.')
        else:
            self.semantics._print_semantics()

    def train(self):
        self.perception.train()
        self.syntax.train()
        # self.semantics.train()
    
    def eval(self):
        self.perception.eval()
        self.syntax.eval()
        # self.semantics.eval()

    def to(self, device):
        self.perception.to(device)
        self.syntax.to(device)
    
    def deduce(self, sample):
        config = self.config
        img_seq = sample['img_seq']
        lengths = sample['len']
        img_seq = img_seq.to(DEVICE)

        if config.perception: # use gt perception
            sentences = sample['sentence']
            sent_probs = np.ones(sentences.shape + (len(SYMBOLS),))
        else:
            sentences, sent_probs = self.perception(img_seq)
            sentences = sentences.detach()
            sent_probs = sent_probs.detach().cpu().numpy()
        sent_probs = [x[:l] for x, l in zip(sent_probs, lengths)]
        sentences = [list(x[:l]) for x, l in zip(sentences.cpu().numpy(), lengths)]

        if config.syntax: # use gt parse
            parses = []
            for s, head in zip(sentences, sample['head']):
                pt = syntax.PartialParse(s)
                pt.head = head
                parses.append(pt)
        else:
            parses = self.syntax(sentences)

        semantics = self.semantics()
        
        self.ASTs = [AST(pt, semantics, s_prob) for pt, s_prob in zip(parses, sent_probs)]
        results = [x.res() for x in self.ASTs]
        head = [pt.head for pt in parses]
        return results, sentences, head

    def abduce(self, gt_values, batch_img_paths):
        for et, y, img_paths in zip(self.ASTs, gt_values.numpy(), batch_img_paths):
            new_et = et.abduce(y, self.learned_module)
            if new_et: 
                new_et.img_paths = img_paths
                self.buffer.append(new_et)
    
    def clear_buffer(self):
        self.buffer = []

    def learn(self):
        if len(self.buffer) == 0: 
            return

        self.train()
        print("Hit samples: ", len(self.buffer), ' Ave length: ', round(np.mean([len(x.pt.sentence) for x in self.buffer]), 2))
        pred_symbols = Counter([y for x in self.buffer for y in x.pt.sentence])
        print("Symbols: ", len(pred_symbols), sorted(pred_symbols.items()))
        pred_heads = Counter([tuple(ast.pt.head) for ast in self.buffer])
        print("Head: ", sorted(pred_heads.most_common(10), key=lambda x: len(x[0])))

        if self.learned_module == 'perception':
            dataset = [(img, label) for x in self.buffer for img, label in zip(x.img_paths, x.pt.sentence)]
            n_iters = int(100)
            print("Learn perception with %d samples for %d iterations, "%(len(self.buffer), n_iters), end='', flush=True)
            st = time()
            self.perception.learn(dataset, n_iters=n_iters)
            print("take %d sec."%(time()-st))

        elif self.learned_module == 'syntax':
            dataset = [x.pt for x in self.buffer]
            n_iters = int(100)
            print("Learn syntax with %d samples for %d iterations, "%(len(self.buffer), n_iters), end='', flush=True)
            st = time()
            self.syntax.learn(dataset, n_iters=n_iters)
            print("take %d sec."%(time()-st))

        elif self.learned_module == 'semantics':
            dataset = [[] for _ in range(len(self.semantics.semantics))]
            for ast in self.buffer:
                queue = [ast.root_node]
                while len(queue) > 0:
                    node = queue.pop()
                    queue.extend(node.children)
                    xs = tuple([x.res() for x in node.children])
                    y = int(node.res())
                    dataset[node.symbol].append((xs, y))
            self.semantics.learn(dataset)

        self.clear_buffer()

if __name__ == '__main__':
    # from utils import SEMANTICS
    # sentences = ['5!-7-4', '1+5!*8', '8*9!+5+1/9/3!*9*5']
    # head = [[1, 2, 4, 2, -1, 4], [1, -1, 3, 4, 1, 4], [1, 4, 3, 1, 6, 4, -1, 8, 10, 8, 13, 12, 10, 15, 13, 6, 15]]
    # for s, dep in zip(sentences, head):
    #     et = AST(s, dep, SEMANTICS)
    #     print(et.res())

    model = Jointer(None)
    from dataset import HINT, HINT_collate
    from torch.utils.data import DataLoader
    from tqdm import tqdm
    dataset = HINT('train')
    data_loader = DataLoader(dataset, batch_size=32,
                         shuffle=True, num_workers=4, collate_fn=HINT_collate)
    model.train()
    for sample in tqdm(data_loader):
        # sample = next(iter(val_loader))
        res = model.deduce(sample['img_seq'], sample['len'])
        model.abduce(sample['res'], sample['img_paths'])
        # print(len([1 for x, y in zip(res, sample['res']) if x is not None and x == y]), len(model.buffer))
        # model.clear_buffer()
        model.learn()
    print(len(model.buffer))
