import argparse
import os
import sys

import pandas
import pandas as pd
import tqdm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append('/home/xiaomeng/jupyter_base/AutoFS/code')
from utils.tools import test_task_new
import pickle
import random
import sys
from typing import List

import numpy as np
import torch
import torch.backends.cudnn as cudnn
import torch.nn.functional as F
import torch.utils
from torch import Tensor
from torch.utils.data import DataLoader
from ours.controller import GAFS
from feature_env import FeatureEvaluator, base_path, MEASUREMENT
from ours.utils_meter import AvgrageMeter, pairwise_accuracy, hamming_distance, count_parameters_in_MB, FSDataset
from record import SelectionRecord
from utils.logger import info, error

parser = argparse.ArgumentParser()
# Basic model parameters.

parser.add_argument('--seed', type=int, default=0)
parser.add_argument('--new_gen', type=int, default=200)
parser.add_argument('--method_name', type=str, choices=['rnn'], default='rnn')
parser.add_argument('--task_name', type=str, choices=['tcga', 'spectf', 'svmguide3', 'german_credit', 'spam_base',
                                                      'ionosphere', 'megawatt1', 'uci_credit_card', 'openml_618',
                                                      'openml_589', 'openml_616', 'openml_607', 'openml_620',
                                                      'openml_637',
                                                      'openml_586', 'uci_credit_card', 'higgs', 'ap_omentum_ovary','activity'
                                                      , 'mice_protein', 'coil-20', 'isolet', 'minist', 'minist_fashion'], default='activity')
parser.add_argument('--gpu', type=int, default=7, help='used gpu')
parser.add_argument('--fe', type=str, choices=['+', '', '-'], default='-')
parser.add_argument('--top_k', type=int, default=100)
parser.add_argument('--gen_num', type=int, default=0)
parser.add_argument('--encoder_layers', type=int, default=1)
parser.add_argument('--encoder_hidden_size', type=int, default=64)
parser.add_argument('--encoder_emb_size', type=int, default=32)
parser.add_argument('--mlp_layers', type=int, default=2)
parser.add_argument('--mlp_hidden_size', type=int, default=200)
parser.add_argument('--decoder_layers', type=int, default=1)
parser.add_argument('--decoder_hidden_size', type=int, default=64)
# parser.add_argument('--source_length', type=int, default=40)
# parser.add_argument('--encoder_length', type=int, default=20)
# parser.add_argument('--decoder_length', type=int, default=40)
parser.add_argument('--encoder_dropout', type=float, default=0)
parser.add_argument('--mlp_dropout', type=float, default=0)
parser.add_argument('--decoder_dropout', type=float, default=0)
parser.add_argument('--l2_reg', type=float, default=0.0)
# parser.add_argument('--encoder_vocab_size', type=int, default=12)
# parser.add_argument('--decoder_vocab_size', type=int, default=12)
parser.add_argument('--max_step_size', type=int, default=100)
parser.add_argument('--trade_off', type=float, default=0.8)
parser.add_argument('--epochs', type=int, default=200)
parser.add_argument('--batch_size', type=int, default=1024)
parser.add_argument('--lr', type=float, default=0.001)
parser.add_argument('--optimizer', type=str, default='adam')
parser.add_argument('--grad_bound', type=float, default=5.0)
# parser.add_argument('--expand', type=int, default=None)
# parser.add_argument('--iteration', type=int, default=0)
# parser.add_argument('--generate_topk', type=int, default=100)
# parser.add_argument('--remain_topk', type=int, default=100)
args = parser.parse_args()
baseline_name = [
    'kbest',
    'mrmr',
    'lasso',
    'rfe',
    # 'gfs',
    'lassonet',
    'sarlfs',
    'marlfs',

]

def gen_ran_record(N_feature):
    action_list = np.random.randint(2, size=N_feature)
    while sum(action_list) < 2:
        action_list = np.random.randint(2, size=N_feature)
    return action_list

def gafs_train(train_queue, model: GAFS, optimizer):
    objs = AvgrageMeter()
    mse = AvgrageMeter()
    nll = AvgrageMeter()
    model.train()
    for step, sample in enumerate(train_queue):
        encoder_input = sample['encoder_input']
        encoder_target = sample['encoder_target']
        decoder_input = sample['decoder_input']
        decoder_target = sample['decoder_target']

        encoder_input = encoder_input.cuda(model.gpu)
        encoder_target = encoder_target.cuda(model.gpu).requires_grad_()
        decoder_input = decoder_input.cuda(model.gpu)
        decoder_target = decoder_target.cuda(model.gpu)

        optimizer.zero_grad()
        predict_value, log_prob, arch = model.forward(encoder_input, decoder_input)
        loss_1 = F.mse_loss(predict_value.squeeze(), encoder_target.squeeze()) # mse loss
        loss_2 = F.nll_loss(log_prob.contiguous().view(-1, log_prob.size(-1)), decoder_target.view(-1)) # ce loss
        loss = args.trade_off * loss_1 + (1 - args.trade_off) * loss_2
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_bound)
        optimizer.step()

        n = encoder_input.size(0)
        objs.update(loss.data, n)
        mse.update(loss_1.data, n)
        nll.update(loss_2.data, n)

    return objs.avg, mse.avg, nll.avg


def gafs_valid(queue, model: GAFS):
    pa = AvgrageMeter()
    hs = AvgrageMeter()
    mse = AvgrageMeter()
    with torch.no_grad():
        model.eval()
        for step, sample in enumerate(queue):
            encoder_input = sample['encoder_input']
            encoder_target = sample['encoder_target']
            decoder_target = sample['decoder_target']

            encoder_input = encoder_input.cuda(model.gpu)
            encoder_target = encoder_target.cuda(model.gpu)
            decoder_target = decoder_target.cuda(model.gpu)

            predict_value, logits, arch = model.forward(encoder_input)
            n = encoder_input.size(0)
            pairwise_acc = pairwise_accuracy(encoder_target.data.squeeze().tolist(),
                                             predict_value.data.squeeze().tolist())
            hamming_dis = hamming_distance(decoder_target.data.squeeze().tolist(), arch.data.squeeze().tolist())
            mse.update(F.mse_loss(predict_value.data.squeeze(), encoder_target.data.squeeze()), n)
            pa.update(pairwise_acc, n)
            hs.update(hamming_dis, n)
    return mse.avg, pa.avg, hs.avg


def choice_to_onehot(choice: List[int]):
    size = len(choice)
    onehot = torch.zeros(size + 1)
    onehot[torch.tensor(choice)] = 1
    return onehot[:-1]
    # if choice.dim() == 1:
    #     selected = torch.zeros_like(choice)
    #     selected[choice] = 1
    #     return selected[1:-1]
    # else:
    #     onehot = torch.empty_like(choice)
    #     for i in range(choice.shape[0]):
    #         onehot[i] = choice_to_onehot(choice[i])
    #     return onehot


def gafs_infer(queue, model, step, direction='+'):
    new_gen_list = []
    model.eval()
    for i, sample in enumerate(queue):
        encoder_input = sample['encoder_input']
        encoder_input = encoder_input.cuda(model.gpu)
        model.zero_grad()
        new_gen = model.generate_new_feature(encoder_input, predict_lambda=step, direction=direction)
        new_gen_list.extend(new_gen.data.squeeze().tolist())
    return new_gen_list


def select_top_k(choice: Tensor, labels: Tensor, k: int) -> (Tensor, Tensor):
    values, indices = torch.topk(labels, k, dim=0)
    return choice[indices.squeeze()], labels[indices.squeeze()]


def main():
    if not torch.cuda.is_available():
        info('No GPU found!')
        sys.exit(1)
    # os.environ['CUDA_VISIBLE_DEVICES'] = ','.join(str(x) for x in args.gpu)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    cudnn.enabled = True
    cudnn.benchmark = False
    cudnn.deterministic = True
    device = int(args.gpu)
    info(f"Args = {args}")

    with open(f'{base_path}/history/{args.task_name}/fe.pkl', 'rb') as f:
        fe: FeatureEvaluator = pickle.load(f)
    model = GAFS(fe, args)

    info(f"param size = {count_parameters_in_MB(model)}MB")
    model = model.cuda(device)

    choice, labels = fe.get_record(args.gen_num, eos=fe.ds_size)
    valid_choice, valid_labels = fe.get_record(0, eos=fe.ds_size)

    info('Training Encoder-Predictor-Decoder')

    min_val = min(labels)
    max_val = max(labels)
    train_encoder_target = [(i - min_val) / (max_val - min_val) for i in labels]
    valid_encoder_target = [(i - min_val) / (max_val - min_val) for i in valid_labels]

    train_dataset = FSDataset(choice, train_encoder_target, train=True, sos_id=fe.ds_size, eos_id=fe.ds_size)
    valid_dataset = FSDataset(valid_choice, valid_encoder_target, train=False, sos_id=fe.ds_size, eos_id=fe.ds_size)
    train_queue = torch.utils.data.DataLoader(
        train_dataset, batch_size=args.batch_size, shuffle=True, pin_memory=True)
    valid_queue = torch.utils.data.DataLoader(
        valid_dataset, batch_size=len(valid_dataset), shuffle=False, pin_memory=True)
    nao_optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.l2_reg)
    for _ in tqdm.tqdm(range(1, args.epochs + 1)):
        gafs_train(train_queue, model, nao_optimizer)

    top_selection, top_performance = select_top_k(valid_choice, valid_labels, args.top_k)

    infer_dataset = FSDataset(top_selection, top_performance, False, sos_id=fe.ds_size, eos_id=fe.ds_size)
    infer_queue = DataLoader(infer_dataset, batch_size=len(infer_dataset), shuffle=False,
                             pin_memory=True)
    new_selection = []
    predict_step_size = 0
    while len(new_selection) < args.new_gen:
        predict_step_size += 1
        info('Generate new architectures with step size {:d}'.format(predict_step_size))
        new_record = gafs_infer(infer_queue, model, direction='+', step=predict_step_size)
        for choice in new_record:
            onehot_choice = choice_to_onehot(choice)
            if onehot_choice.sum() <= 0:
                record = SelectionRecord(gen_ran_record(fe.ds_size), -1)
                new_selection.append(record)
                error('insufficient selection')
                continue
            record = SelectionRecord(onehot_choice.numpy(), -1)
            if record not in fe.records.r_list and record not in new_selection:
                new_selection.append(record)
            else:
                record = SelectionRecord(gen_ran_record(fe.ds_size), -1)
                new_selection.append(record)
            if len(new_selection) >= args.new_gen:
                break
        info(f'{len(new_selection)} new choice generated now', )
        if predict_step_size > args.max_step_size:
            break
    info(f'build {len(new_selection)} new choice !!!')
    best_selection = None
    best_optimal = -1000
    best_selection_test = None
    best_optimal_test = -1000

    for s in new_selection:
        train_data = fe.generate_data(s.operation, 'train')
        result = fe.get_performance(train_data)
        test_data = fe.generate_data(s.operation, 'test')
        test_result = fe.get_performance(test_data)

        if result > best_optimal:
            best_selection = s.operation
            best_optimal = result
            info(f'found best on train : {best_optimal}')
        if test_result > best_optimal_test:
            best_selection_test = s.operation
            best_optimal_test = test_result
            info(f'found best on test : {best_optimal_test}')

    opt_path = f'{base_path}/history/{fe.task_name}/best-ours-zero.hdf'
    # ori_p = fe.report_performance(best_selection, flag='test')
    # info(f'found train generation in our method! the choice is {best_selection}, the performance is {ori_p}')
    fe.generate_data(best_selection, 'train').to_hdf(opt_path, key='train')
    fe.generate_data(best_selection, 'test').to_hdf(opt_path, key='test')

    opt_path_test = f'{base_path}/history/{fe.task_name}/best-ours-zero-test.hdf'
    # test_p = fe.report_performance(best_selection_test, flag='test')
    # info(f'found test generation in our method! the choice is {best_selection_test}, the performance is {test_p}')
    fe.generate_data(best_selection_test, 'train').to_hdf(opt_path_test, key='train')
    fe.generate_data(best_selection_test, 'test').to_hdf(opt_path_test, key='test')
    info('given overall validation')
    report_head = 'metric\t ours\t zero\t zero_test\n'
    ours_test = pandas.read_hdf(f'{base_path}/history/{fe.task_name}.hdf', key='our_test')
    random_ds = fe.generate_data(best_selection, 'test')
    random_ds_test = fe.generate_data(best_selection_test, 'test')
    metrics = MEASUREMENT[fe.task_type]
    a_1, b_1, c_1, d_1 = test_task_new(ours_test, fe.task_type)
    a_2, b_2, c_2, d_2 = test_task_new(random_ds, fe.task_type)
    a_3, b_3, c_3, d_3 = test_task_new(random_ds_test, fe.task_type)
    r1 = [a_1, b_1, c_1, d_1]
    r2 = [a_2, b_2, c_2, d_2]
    r3 = [a_3, b_3, c_3, d_3]
    print(report_head)
    for indice, name in enumerate(metrics):
        print(f'{name}\t{r1[indice]}\t{r2[indice]}\t{r3[indice]}')
    print('done zero exp!')

# /home/xiaomeng/miniconda3/envs/shaow/bin/python3 -u /home/xiaomeng/jupyter_base/AutoFS/code/ours/exps/RQ2:NonInvariant/train_controller_noninv.py --task_name mice_protein --gpu 5
# /home/xiaomeng/miniconda3/envs/shaow/bin/python3 -u /home/xiaomeng/jupyter_base/AutoFS/code/ours/exps/RQ2:NonInvariant/train_controller_noninv.py --task_name openml_589 --gpu 5
# /home/xiaomeng/miniconda3/envs/shaow/bin/python3 -u /home/xiaomeng/jupyter_base/AutoFS/code/ours/exps/RQ2:NonInvariant/train_controller_noninv.py --task_name german_credit --gpu 5
# /home/xiaomeng/miniconda3/envs/shaow/bin/python3 -u /home/xiaomeng/jupyter_base/AutoFS/code/ours/exps/RQ2:NonInvariant/train_controller_noninv.py --task_name spectf --gpu 5

if __name__ == '__main__':
    task_name = 'mice_protein'
    opt_path_test = f'{base_path}/history/{task_name}/best-ours-zero-test.hdf'
    opt_path = f'{base_path}/history/{task_name}/best-ours-zero.hdf'
    random_test = pd.read_hdf(opt_path_test, 'test')
    random = pd.read_hdf(opt_path, 'test')
    ours_test = pandas.read_hdf(f'{base_path}/history/{task_name}.hdf', key='our_test')
    metrics = MEASUREMENT['mcls']
    report_head = 'metric\t ours\t zero\t zero_test\n'
    a_1, b_1, c_1, d_1 = test_task_new(ours_test, 'mcls')
    a_2, b_2, c_2, d_2 = test_task_new(random, 'mcls')
    a_3, b_3, c_3, d_3 = test_task_new(random_test, 'mcls')
    r1 = [a_1, b_1, c_1, d_1]
    r2 = [a_2, b_2, c_2, d_2]
    r3 = [a_3, b_3, c_3, d_3]
    print(report_head)
    for indice, name in enumerate(metrics):
        print(f'{name}\t{r1[indice]}\t{r2[indice]}\t{r3[indice]}')
    print('done zero exp!')
