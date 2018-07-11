from __future__ import print_function

import torch.nn as nn
import torch.nn.functional as F
from torch.nn import Module, NLLLoss


def _hard_negative_mining(cls_loss, pos):
    """Return negative indices that is 3x the number as postive indices.
    Args:
      cls_loss: (tensor) cross entropy losses between cls_preds and cls_targets, sized [N,#anchors].
      pos: (tensor) positive class mask, sized [N,#anchors].
    Return:
      (tensor) negative indices, sized [N,#anchors].
    """
    cls_loss = cls_loss * (pos.float() - 1)

    _, idx = cls_loss.sort(1)  # sort by negative losses
    _, rank = idx.sort(1)      # [N,#anchors]

    num_neg = 3*pos.sum(1)  # [N,]
    neg = rank < num_neg[:,None]   # [N,#anchors]
    return neg


# class SmoothL1Loss(nn.Module):
#     def forward(self, input, target):
#         """
#           :param input: (tensor) predicted locations, sized [N, #anchors, 4].
#           :param target: (tensor) encoded target locations, sized [N, #anchors, 4].
#         """


class SSDLoss(nn.Module):
    def __init__(self, num_classes):
        super(SSDLoss, self).__init__()
        self.num_classes = num_classes

    def forward(self, loc_preds, loc_targets, cls_preds, cls_targets):
        """Compute losses between (loc_preds, loc_targets) and (cls_preds, cls_targets).
        Args:
          loc_preds: (tensor) predicted locations, sized [N, #anchors, 4].
          loc_targets: (tensor) encoded target locations, sized [N, #anchors, 4].
          cls_preds: (tensor) predicted class confidences, sized [N, #anchors, #classes].
          cls_targets: (tensor) encoded target labels, sized [N, #anchors].
        losses:
          (tensor) losses = SmoothL1Loss(loc_preds, loc_targets) + CrossEntropyLoss(cls_preds, cls_targets).
        """
        pos = cls_targets > 0  # [N,#anchors]
        batch_size = pos.size(0)
        num_pos = pos.sum().item()

        # loc_loss = SmoothL1Loss(pos_loc_preds, pos_loc_targets)
        mask = pos.unsqueeze(2).expand_as(loc_preds)       # [N,#anchors,4]
        loc_loss = F.smooth_l1_loss(loc_preds[mask], loc_targets[mask], size_average=False)

        # cls_loss = CrossEntropyLoss(cls_preds, cls_targets)
        # print(cls_preds.shape, cls_preds.max(), cls_preds.min())
        cls_loss = F.nll_loss(cls_preds, cls_targets, reduce=False) # [N*#anchors,]
        neg = _hard_negative_mining(cls_loss, pos)  # [N,#anchors]
        cls_loss = cls_loss[pos|neg].sum()

        # print('loc_loss: %.3f | cls_loss: %.3f' % (loc_loss.item()/num_pos, cls_loss.item()/num_pos), end=' | ')
        loss = (loc_loss+cls_loss)/num_pos
        return loss