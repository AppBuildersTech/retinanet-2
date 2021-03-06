# Based on https://github.com/selimsef/dsb2018_topcoders/blob/master/albu/src/pytorch_utils/callbacks.py
import torch
from copy import deepcopy
import os
from tensorboardX import SummaryWriter


class Callback(object):
    """
    Abstract base class used to build new callbacks.
    """

    def __init__(self):
        self.runner = None
        self.metrics = None

    def set_trainer(self, runner):
        self.runner = runner
        self.metrics = runner.metrics

    def on_batch_begin(self, i, **kwargs):
        pass

    def on_batch_end(self, i, **kwargs):
        pass

    def on_epoch_begin(self, epoch):
        pass

    def on_epoch_end(self, epoch):
        pass

    def on_train_begin(self):
        pass

    def on_train_end(self):
        pass


class Callbacks(Callback):
    def __init__(self, callbacks):
        super().__init__()
        if isinstance(callbacks, Callbacks):
            callbacks = callbacks.callbacks
        self.callbacks = callbacks
        if callbacks is None:
            self.callbacks = []

    def set_trainer(self, runner):
        for callback in self.callbacks:
            callback.set_trainer(runner)

    def on_batch_begin(self, i, **kwargs):
        for callback in self.callbacks:
            callback.on_batch_begin(i, **kwargs)

    def on_batch_end(self, i, **kwargs):
        for callback in self.callbacks:
            callback.on_batch_end(i, **kwargs)

    def on_epoch_begin(self, epoch):
        for callback in self.callbacks:
            callback.on_epoch_begin(epoch)

    def on_epoch_end(self, epoch):
        for callback in self.callbacks:
            callback.on_epoch_end(epoch)

    def on_train_begin(self):
        for callback in self.callbacks:
            callback.on_train_begin()

    def on_train_end(self):
        for callback in self.callbacks:
            callback.on_train_end()


class ModelRestorer(Callback):
    def __init__(self, checkpoint_path):
        super().__init__()
        self.checkpoint_path = checkpoint_path

    def on_train_begin(self):
        state = torch.load(self.checkpoint_path, map_location=self.runner.device)
        self.runner.model.module.load_state_dict(state['state_dict'])
        self.runner.optimizer.load_state_dict(state['optimizer'])


class ModelSaver(Callback):
    def __init__(self, save_every, save_name, best_only=True):
        super().__init__()
        self.save_every = save_every
        self.save_name = save_name
        self.best_only = best_only

    def on_epoch_end(self, epoch):
        loss = float(self.metrics.val_metrics['loss'])
        need_save = not self.best_only
        if epoch % self.save_every == 0:
            if loss < self.metrics.best_loss:
                self.metrics.best_loss = loss
                self.metrics.best_epoch = epoch
                need_save = True

            if need_save:
                path = os.path.join(self.runner.model_dir, self.save_name).format(epoch=epoch,
                                                                                  loss="{:.2}".format(loss))
                torch.save(obj=deepcopy(self.runner.model.module), f=path)


def save_checkpoint(epoch, model_state_dict, optimizer_state_dict, path):
    torch.save({
        'epoch': epoch + 1,
        'state_dict': model_state_dict,
        'optimizer': optimizer_state_dict,
    }, path)


class CheckpointSaver(Callback):
    def __init__(self, save_every, save_name):
        super().__init__()
        self.save_every = save_every
        self.save_name = save_name

    def on_epoch_end(self, epoch):
        loss = float(self.metrics.val_metrics['loss'])
        if epoch % self.save_every == 0:
            path = os.path.join(self.runner.model_dir, self.save_name).format(epoch=epoch, loss="{:.2}".format(loss))
            save_checkpoint(
                epoch=epoch,
                model_state_dict=self.runner.model.module.state_dict(),
                optimizer_state_dict=self.runner.optimizer.state_dict(),
                path=path)


class TensorBoard(Callback):
    def __init__(self, log_dir):
        super().__init__()
        self.log_dir = log_dir
        self.writer = None

    def on_train_begin(self):
        path = os.path.join(self.log_dir, self.runner.name)
        os.makedirs(path, exist_ok=True)
        self.writer = SummaryWriter(path)

    def on_epoch_end(self, epoch):
        for k, v in self.metrics.train_metrics.items():
            self.writer.add_scalar('train/{}'.format(k), float(v), global_step=epoch)

        for k, v in self.metrics.val_metrics.items():
            self.writer.add_scalar('val/{}'.format(k), float(v), global_step=epoch)

        for idx, param_group in enumerate(self.runner.optimizer.param_groups):
            lr = param_group['lr']
            self.writer.add_scalar('group{}/lr'.format(idx), float(lr), global_step=epoch)

    def on_train_end(self):
        self.writer.close()


class OneCycleLR(Callback):
    """
    An learning rate updater
        that implements the CircularLearningRate (CLR) scheme.
    Learning rate is increased then decreased linearly.

    https://github.com/Scitator/pytorch-common/blob/master/train/callbacks.py
    """

    def __init__(self, init_lr, cycle_len, div, cut_div, momentum_range, len_loader):
        """
        :param init_lr: init learning rate for torch optimizer
        :param cycle_len: (int) num epochs to apply one cycle policy
        :param div: (int) ratio between initial lr and maximum lr
        :param cut_div: (int) which part of cycle lr will grow
            (Ex: cut_div=4 -> 1/4 lr grow, 3/4 lr decrease
        :param momentum_range: (tuple(int, int)) max and min momentum values
        """
        super().__init__()
        self.init_lr = init_lr
        self.len_loader = len_loader
        self.total_iter = None
        self.div = div
        self.cut_div = cut_div
        self.cycle_iter = 0
        self.cycle_count = 0
        self.cycle_len = cycle_len
        # point in iterations for starting lr decreasing
        self.cut_point = None
        self.momentum_range = momentum_range

    def calc_lr(self):
        # calculate percent for learning rate change
        if self.cycle_iter > self.cut_point:
            percent = 1 - (self.cycle_iter - self.cut_point) / (self.total_iter - self.cut_point)
        else:
            percent = self.cycle_iter / self.cut_point
        res = self.init_lr * (1 + percent * (self.div - 1)) / self.div

        self.cycle_iter += 1
        if self.cycle_iter == self.total_iter:
            self.cycle_iter = 0
            self.cycle_count += 1
        return res

    def calc_momentum(self):
        if self.cycle_iter > self.cut_point:
            percent = (self.cycle_iter - self.cut_point) / (self.total_iter - self.cut_point)
        else:
            percent = 1 - self.cycle_iter / self.cut_point
        res = self.momentum_range[1] + percent * (self.momentum_range[0] - self.momentum_range[1])
        return res

    def update_lr(self, optimizer):
        new_lr = self.calc_lr()
        for pg in optimizer.param_groups:
            pg["lr"] = new_lr
        return new_lr

    def update_momentum(self, optimizer):
        new_momentum = self.calc_momentum()
        if "betas" in optimizer.param_groups[0]:
            for pg in optimizer.param_groups:
                pg["betas"] = (new_momentum, pg["betas"][1])
        else:
            for pg in optimizer.param_groups:
                pg["momentum"] = new_momentum
        return new_momentum

    def on_batch_end(self, i, **kwargs):
        if kwargs['is_train']:
            self.update_lr(self.runner.optimizer)
            self.update_momentum(self.runner.optimizer)

    def on_train_begin(self):
        self.total_iter = self.len_loader * self.cycle_len
        self.cut_point = self.total_iter // self.cut_div

        self.update_lr(self.runner.optimizer)
        self.update_momentum(self.runner.optimizer)


class LRFinder(Callback):
    """
    https://sgugger.github.io/how-do-you-find-a-good-learning-rate.html
    """

    def __init__(self, len_loader, init_lr, final_lr, beta, save_name):
        super().__init__()
        self.save_name = save_name
        self.beta = beta
        self.final_lr = final_lr
        self.init_lr = init_lr
        self.len_loader = len_loader
        self.multiplier = (self.final_lr / self.init_lr) ** (1 / self.len_loader)
        self.avg_loss = 0.0
        self.best_loss = 0.0
        self.find_iter = 0
        self.losses = []
        self.log_lrs = []
        self.is_find = False

    def calc_lr(self):
        res = self.init_lr * self.multiplier ** self.find_iter
        self.find_iter += 1
        return res

    def update_lr(self, optimizer):
        new_lr = self.calc_lr()
        for pg in optimizer.param_groups:
            pg["lr"] = new_lr
        return new_lr

    def on_batch_end(self, i, **kwargs):
        loss = kwargs['step_report']['loss'].item()
        self.avg_loss = self.beta * self.avg_loss + (1 - self.beta) * loss
        smoothed_loss = self.avg_loss / (1 - self.beta ** self.find_iter)

        if smoothed_loss < self.best_loss or self.find_iter == 1:
            self.best_loss = smoothed_loss

        if not self.is_find:
            self.losses.append(smoothed_loss)
            self.log_lrs.append(self.update_lr(self.runner.optimizer))

        if self.find_iter > 1 and smoothed_loss > 4 * self.best_loss:
            self.is_find = True

    def on_train_begin(self):
        self.update_lr(self.runner.optimizer)

    def on_train_end(self):
        torch.save({
            'best_loss': self.best_loss,
            'log_lrs': self.log_lrs,
            'losses': self.losses,
        }, os.path.join(self.runner.model_dir, self.save_name))
