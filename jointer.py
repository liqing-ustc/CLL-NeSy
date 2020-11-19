import perception, syntax, semantics
import numpy as np
from copy import deepcopy
import sys
from func_timeout import func_timeout, FunctionTimedOut
from utils import SYMBOLS

class Node:
    def __init__(self, symbol, sym2prog):
        self.symbol = symbol
        self.sym2prog = sym2prog
        self.children = []
        self._res= None

    def res(self):
        if self._res != None:
            return self._res

        prog = self.sym2prog[self.symbol][0]
        self._res = int(prog(*[x.res() for x in self.children]))
        if self._res > sys.maxsize:
            self._res = None
        return self._res

class AST: # Abstract Syntax Tree
    def __init__(self, sentence, dependencies, sym2prog, transitions=None, sent_probs=None, transition_probs=None):
        self.sentence = sentence
        self.dependencies = dependencies
        self.transitions = transitions
        self.sent_probs = sent_probs
        self.transition_probs = transition_probs
        self.sym2prog = sym2prog

        nodes = [Node(s, sym2prog) for s in sentence]
        for node, h in zip(nodes, dependencies):
            if h == -1:
                root_node = node
                continue
            nodes[h].children.append(node)
        self.nodes = nodes
        self.root_node = root_node

        self._res = None
        try:
            # TODO: set a timeout for the execution
            # self._res = func_timeout(timeout=0.01, func=root_node.res)
            self._res = root_node.res()
        except (TypeError, ZeroDivisionError, ValueError, RecursionError, FunctionTimedOut) as e:
            # if isinstance(e, FunctionTimedOut):
            #     print(e)
            pass

    def res(self): return self._res

    def abduce(self, y):
        if self._res is not None and self._res == y:
            et = AST(self.sentence, self.dependencies, self.sym2prog)
            return et
        
        epsilon = 1e-5
        # abduce over sentence
        sent_pos_list = np.argsort([self.sent_probs[i, s] for i, s in enumerate(self.sentence)])
        for sent_pos in sent_pos_list:
            s_prob = self.sent_probs[sent_pos]
            if s_prob[self.sentence[sent_pos]] > 1 - epsilon:
                break
            for sym_pos in np.argsort(s_prob)[::-1]:
                if s_prob[sym_pos] < epsilon:
                    break
                new_sentence = deepcopy(self.sentence)
                new_sentence[sent_pos] = sym_pos
                et = AST(new_sentence, self.dependencies, self.sym2prog)
                if et.res() is not None and et.res() == y:
                    return et

        # abduce over parse
        # if current trans is 'S', we try to swith it with the next token
        # if current trans is 'L' ('R'), we try to switch it to 'R' ('L')
        trans_pos_list = np.argsort([self.transition_probs[i][t] for i, t in enumerate(self.transitions)])
        for trans_pos in trans_pos_list:
            t_prob = self.transition_probs[trans_pos]
            t_ori = self.transitions[trans_pos]
            if t_prob[t_ori] > 1 - epsilon:
                break

            if t_ori == 0: # Left-Arc
                new_transitions = deepcopy(self.transitions)
                new_transitions[trans_pos] = 1
            elif t_ori == 1: # Right-Arc
                new_transitions = deepcopy(self.transitions)
                new_transitions[trans_pos] = 0
            elif t_ori == 2: # Shift
                if self.transitions[trans_pos + 1] == 2: # both current trans and next trans are 'S', so skip
                    continue
                else: # swith current trans and next trans
                    new_transitions = deepcopy(self.transitions)
                    new_transitions[trans_pos] = new_transitions[trans_pos+1]
                    new_transitions[trans_pos+1] = t_ori
            dependencies = syntax.convert_trans2dep(new_transitions)
            et = AST(self.sentence, dependencies, self.sym2prog)
            if et.res() is not None and et.res() == y:
                return et

        # abduce over semantics
        for s in list(set(self.sentence)): # a better rank?
            # We cannot use 'deepcopy' here since it is very slow
            # sym2prog = deepcopy(self.sym2prog)
            sym2prog = []
            for programs in self.sym2prog:
                programs = [x for x in programs]
                sym2prog.append(programs)
            
            while len(sym2prog[s]) > 1:
                sym2prog[s].pop(0)
                et = AST(self.sentence, self.dependencies, sym2prog)
                if et.res() is not None and et.res() == y:
                    return et

        return None

    
class Jointer:
    def __init__(self, config=None):
        super(Jointer, self).__init__()
        self.perception = perception.build(config)
        self.syntax = syntax.build(config)
        self.semantics = semantics.build(config)
        self.ASTs = []
        self.buffer = []

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
    
    def deduce(self, img_seqs, lengths):
        sentences, sent_probs = self.perception(img_seqs)
        sentences = sentences.detach()
        sent_probs = sent_probs.detach()
        sentences = [x[:l] for x, l in zip(sentences, lengths)]
        sent_probs = [x[:l] for x, l in zip(sent_probs, lengths)]
        parses = self.syntax(sentences)
        sym2prog = self.semantics()
        
        self.ASTs = []
        for s_prob, pt in zip(sent_probs, parses):
            et = AST(pt.sentence.cpu().numpy(), pt.dependencies, sym2prog, pt.transitions, s_prob.cpu().numpy(), pt.probs)
            self.ASTs.append(et)
        results = [x.res() for x in self.ASTs]

        dependencies = [pt.dependencies for pt in parses]
        return sentences, dependencies, results
    
    def abduce(self, gt_values, batch_img_paths):
        # abduce over perception (sentence) and syntax (parse)
        for et, y, img_paths in zip(self.ASTs, gt_values, batch_img_paths):
            new_et = et.abduce(y)
            if new_et: 
                new_et.img_paths = img_paths
                self.buffer.append(new_et)
    
    def clear_buffer(self):
        self.buffer = []

    def learn(self):
        assert len(self.buffer) > 0
        self.train()
        print("Hit samples: ", len(self.buffer), ' Ave length: ', round(np.mean([len(x.sentence) for x in self.buffer]), 2))

        # learn semantics
        dataset = [[] for _ in range(len(SYMBOLS) - 1)]
        for ast in self.buffer:
            queue = [ast.root_node]
            while len(queue) > 0:
                node = queue.pop()
                queue.extend(node.children)
                xs = tuple([x.res() for x in node.children])
                y = node.res()
                dataset[node.symbol].append((xs, y))
        self.semantics.learn(dataset)

        # learn perception
        dataset = [(x.img_paths, x.sentence) for x in self.buffer]
        self.perception.learn(dataset, n_epochs=5)

        # learn syntax
        dataset = [{'word': x.sentence, 'head': x.dependencies} for x in self.buffer]
        self.syntax.learn(dataset)


        self.clear_buffer()






if __name__ == '__main__':
    # from utils import SYM2PROG
    # sentences = ['5!-7-4', '1+5!*8', '8*9!+5+1/9/3!*9*5']
    # dependencies = [[1, 2, 4, 2, -1, 4], [1, -1, 3, 4, 1, 4], [1, 4, 3, 1, 6, 4, -1, 8, 10, 8, 13, 12, 10, 15, 13, 6, 15]]
    # for s, dep in zip(sentences, dependencies):
    #     et = AST(s, dep, SYM2PROG)
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