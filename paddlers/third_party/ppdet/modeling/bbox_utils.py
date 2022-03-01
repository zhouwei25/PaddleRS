#   Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import math
import paddle
import numpy as np


def bbox2delta(src_boxes, tgt_boxes, weights):
    src_w = src_boxes[:, 2] - src_boxes[:, 0]
    src_h = src_boxes[:, 3] - src_boxes[:, 1]
    src_ctr_x = src_boxes[:, 0] + 0.5 * src_w
    src_ctr_y = src_boxes[:, 1] + 0.5 * src_h

    tgt_w = tgt_boxes[:, 2] - tgt_boxes[:, 0]
    tgt_h = tgt_boxes[:, 3] - tgt_boxes[:, 1]
    tgt_ctr_x = tgt_boxes[:, 0] + 0.5 * tgt_w
    tgt_ctr_y = tgt_boxes[:, 1] + 0.5 * tgt_h

    wx, wy, ww, wh = weights
    dx = wx * (tgt_ctr_x - src_ctr_x) / src_w
    dy = wy * (tgt_ctr_y - src_ctr_y) / src_h
    dw = ww * paddle.log(tgt_w / src_w)
    dh = wh * paddle.log(tgt_h / src_h)

    deltas = paddle.stack((dx, dy, dw, dh), axis=1)
    return deltas


def delta2bbox(deltas, boxes, weights):
    clip_scale = math.log(1000.0 / 16)

    widths = boxes[:, 2] - boxes[:, 0]
    heights = boxes[:, 3] - boxes[:, 1]
    ctr_x = boxes[:, 0] + 0.5 * widths
    ctr_y = boxes[:, 1] + 0.5 * heights

    wx, wy, ww, wh = weights
    dx = deltas[:, 0::4] / wx
    dy = deltas[:, 1::4] / wy
    dw = deltas[:, 2::4] / ww
    dh = deltas[:, 3::4] / wh
    # Prevent sending too large values into paddle.exp()
    dw = paddle.clip(dw, max=clip_scale)
    dh = paddle.clip(dh, max=clip_scale)

    pred_ctr_x = dx * widths.unsqueeze(1) + ctr_x.unsqueeze(1)
    pred_ctr_y = dy * heights.unsqueeze(1) + ctr_y.unsqueeze(1)
    pred_w = paddle.exp(dw) * widths.unsqueeze(1)
    pred_h = paddle.exp(dh) * heights.unsqueeze(1)

    pred_boxes = []
    pred_boxes.append(pred_ctr_x - 0.5 * pred_w)
    pred_boxes.append(pred_ctr_y - 0.5 * pred_h)
    pred_boxes.append(pred_ctr_x + 0.5 * pred_w)
    pred_boxes.append(pred_ctr_y + 0.5 * pred_h)
    pred_boxes = paddle.stack(pred_boxes, axis=-1)

    return pred_boxes


def expand_bbox(bboxes, scale):
    w_half = (bboxes[:, 2] - bboxes[:, 0]) * .5
    h_half = (bboxes[:, 3] - bboxes[:, 1]) * .5
    x_c = (bboxes[:, 2] + bboxes[:, 0]) * .5
    y_c = (bboxes[:, 3] + bboxes[:, 1]) * .5

    w_half *= scale
    h_half *= scale

    bboxes_exp = np.zeros(bboxes.shape, dtype=np.float32)
    bboxes_exp[:, 0] = x_c - w_half
    bboxes_exp[:, 2] = x_c + w_half
    bboxes_exp[:, 1] = y_c - h_half
    bboxes_exp[:, 3] = y_c + h_half

    return bboxes_exp


def clip_bbox(boxes, im_shape):
    h, w = im_shape[0], im_shape[1]
    x1 = boxes[:, 0].clip(0, w)
    y1 = boxes[:, 1].clip(0, h)
    x2 = boxes[:, 2].clip(0, w)
    y2 = boxes[:, 3].clip(0, h)
    return paddle.stack([x1, y1, x2, y2], axis=1)


def nonempty_bbox(boxes, min_size=0, return_mask=False):
    w = boxes[:, 2] - boxes[:, 0]
    h = boxes[:, 3] - boxes[:, 1]
    mask = paddle.logical_and(h > min_size, w > min_size)
    if return_mask:
        return mask
    keep = paddle.nonzero(mask).flatten()
    return keep


def bbox_area(boxes):
    return (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])


def bbox_overlaps(boxes1, boxes2):
    """
    Calculate overlaps between boxes1 and boxes2

    Args:
        boxes1 (Tensor): boxes with shape [M, 4]
        boxes2 (Tensor): boxes with shape [N, 4]

    Return:
        overlaps (Tensor): overlaps between boxes1 and boxes2 with shape [M, N]
    """
    M = boxes1.shape[0]
    N = boxes2.shape[0]
    if M * N == 0:
        return paddle.zeros([M, N], dtype='float32')
    area1 = bbox_area(boxes1)
    area2 = bbox_area(boxes2)

    xy_max = paddle.minimum(
        paddle.unsqueeze(boxes1, 1)[:, :, 2:], boxes2[:, 2:])
    xy_min = paddle.maximum(
        paddle.unsqueeze(boxes1, 1)[:, :, :2], boxes2[:, :2])
    width_height = xy_max - xy_min
    width_height = width_height.clip(min=0)
    inter = width_height.prod(axis=2)

    overlaps = paddle.where(inter > 0, inter /
                            (paddle.unsqueeze(area1, 1) + area2 - inter),
                            paddle.zeros_like(inter))
    return overlaps


def batch_bbox_overlaps(bboxes1,
                        bboxes2,
                        mode='iou',
                        is_aligned=False,
                        eps=1e-6):
    """Calculate overlap between two set of bboxes.
    If ``is_aligned `` is ``False``, then calculate the overlaps between each
    bbox of bboxes1 and bboxes2, otherwise the overlaps between each aligned
    pair of bboxes1 and bboxes2.
    Args:
        bboxes1 (Tensor): shape (B, m, 4) in <x1, y1, x2, y2> format or empty.
        bboxes2 (Tensor): shape (B, n, 4) in <x1, y1, x2, y2> format or empty.
            B indicates the batch dim, in shape (B1, B2, ..., Bn).
            If ``is_aligned `` is ``True``, then m and n must be equal.
        mode (str): "iou" (intersection over union) or "iof" (intersection over
            foreground).
        is_aligned (bool, optional): If True, then m and n must be equal.
            Default False.
        eps (float, optional): A value added to the denominator for numerical
            stability. Default 1e-6.
    Returns:
        Tensor: shape (m, n) if ``is_aligned `` is False else shape (m,)
    """
    assert mode in ['iou', 'iof', 'giou'], 'Unsupported mode {}'.format(mode)
    # Either the boxes are empty or the length of boxes's last dimenstion is 4
    assert (bboxes1.shape[-1] == 4 or bboxes1.shape[0] == 0)
    assert (bboxes2.shape[-1] == 4 or bboxes2.shape[0] == 0)

    # Batch dim must be the same
    # Batch dim: (B1, B2, ... Bn)
    assert bboxes1.shape[:-2] == bboxes2.shape[:-2]
    batch_shape = bboxes1.shape[:-2]

    rows = bboxes1.shape[-2] if bboxes1.shape[0] > 0 else 0
    cols = bboxes2.shape[-2] if bboxes2.shape[0] > 0 else 0
    if is_aligned:
        assert rows == cols

    if rows * cols == 0:
        if is_aligned:
            return paddle.full(batch_shape + (rows, ), 1)
        else:
            return paddle.full(batch_shape + (rows, cols), 1)

    area1 = (bboxes1[:, 2] - bboxes1[:, 0]) * (bboxes1[:, 3] - bboxes1[:, 1])
    area2 = (bboxes2[:, 2] - bboxes2[:, 0]) * (bboxes2[:, 3] - bboxes2[:, 1])

    if is_aligned:
        lt = paddle.maximum(bboxes1[:, :2], bboxes2[:, :2])  # [B, rows, 2]
        rb = paddle.minimum(bboxes1[:, 2:], bboxes2[:, 2:])  # [B, rows, 2]

        wh = (rb - lt).clip(min=0)  # [B, rows, 2]
        overlap = wh[:, 0] * wh[:, 1]

        if mode in ['iou', 'giou']:
            union = area1 + area2 - overlap
        else:
            union = area1
        if mode == 'giou':
            enclosed_lt = paddle.minimum(bboxes1[:, :2], bboxes2[:, :2])
            enclosed_rb = paddle.maximum(bboxes1[:, 2:], bboxes2[:, 2:])
    else:
        lt = paddle.maximum(bboxes1[:, :2].reshape([rows, 1, 2]),
                            bboxes2[:, :2])  # [B, rows, cols, 2]
        rb = paddle.minimum(bboxes1[:, 2:].reshape([rows, 1, 2]),
                            bboxes2[:, 2:])  # [B, rows, cols, 2]

        wh = (rb - lt).clip(min=0)  # [B, rows, cols, 2]
        overlap = wh[:, :, 0] * wh[:, :, 1]

        if mode in ['iou', 'giou']:
            union = area1.reshape([rows,1]) \
                    + area2.reshape([1,cols]) - overlap
        else:
            union = area1[:, None]
        if mode == 'giou':
            enclosed_lt = paddle.minimum(bboxes1[:, :2].reshape([rows, 1, 2]),
                                         bboxes2[:, :2])
            enclosed_rb = paddle.maximum(bboxes1[:, 2:].reshape([rows, 1, 2]),
                                         bboxes2[:, 2:])

    eps = paddle.to_tensor([eps])
    union = paddle.maximum(union, eps)
    ious = overlap / union
    if mode in ['iou', 'iof']:
        return ious
    # calculate gious
    enclose_wh = (enclosed_rb - enclosed_lt).clip(min=0)
    enclose_area = enclose_wh[:, :, 0] * enclose_wh[:, :, 1]
    enclose_area = paddle.maximum(enclose_area, eps)
    gious = ious - (enclose_area - union) / enclose_area
    return 1 - gious


def xywh2xyxy(box):
    x, y, w, h = box
    x1 = x - w * 0.5
    y1 = y - h * 0.5
    x2 = x + w * 0.5
    y2 = y + h * 0.5
    return [x1, y1, x2, y2]


def make_grid(h, w, dtype):
    yv, xv = paddle.meshgrid([paddle.arange(h), paddle.arange(w)])
    return paddle.stack((xv, yv), 2).cast(dtype=dtype)


def decode_yolo(box, anchor, downsample_ratio):
    """decode yolo box

    Args:
        box (list): [x, y, w, h], all have the shape [b, na, h, w, 1]
        anchor (list): anchor with the shape [na, 2]
        downsample_ratio (int): downsample ratio, default 32
        scale (float): scale, default 1.

    Return:
        box (list): decoded box, [x, y, w, h], all have the shape [b, na, h, w, 1]
    """
    x, y, w, h = box
    na, grid_h, grid_w = x.shape[1:4]
    grid = make_grid(grid_h, grid_w, x.dtype).reshape(
        (1, 1, grid_h, grid_w, 2))
    x1 = (x + grid[:, :, :, :, 0:1]) / grid_w
    y1 = (y + grid[:, :, :, :, 1:2]) / grid_h

    anchor = paddle.to_tensor(anchor)
    anchor = paddle.cast(anchor, x.dtype)
    anchor = anchor.reshape((1, na, 1, 1, 2))
    w1 = paddle.exp(w) * anchor[:, :, :, :, 0:1] / (downsample_ratio * grid_w)
    h1 = paddle.exp(h) * anchor[:, :, :, :, 1:2] / (downsample_ratio * grid_h)

    return [x1, y1, w1, h1]


def iou_similarity(box1, box2, eps=1e-9):
    """Calculate iou of box1 and box2

    Args:
        box1 (Tensor): box with the shape [N, M1, 4]
        box2 (Tensor): box with the shape [N, M2, 4]

    Return:
        iou (Tensor): iou between box1 and box2 with the shape [N, M1, M2]
    """
    box1 = box1.unsqueeze(2)  # [N, M1, 4] -> [N, M1, 1, 4]
    box2 = box2.unsqueeze(1)  # [N, M2, 4] -> [N, 1, M2, 4]
    px1y1, px2y2 = box1[:, :, :, 0:2], box1[:, :, :, 2:4]
    gx1y1, gx2y2 = box2[:, :, :, 0:2], box2[:, :, :, 2:4]
    x1y1 = paddle.maximum(px1y1, gx1y1)
    x2y2 = paddle.minimum(px2y2, gx2y2)
    overlap = (x2y2 - x1y1).clip(0).prod(-1)
    area1 = (px2y2 - px1y1).clip(0).prod(-1)
    area2 = (gx2y2 - gx1y1).clip(0).prod(-1)
    union = area1 + area2 - overlap + eps
    return overlap / union


def bbox_iou(box1, box2, giou=False, diou=False, ciou=False, eps=1e-9):
    """calculate the iou of box1 and box2

    Args:
        box1 (list): [x, y, w, h], all have the shape [b, na, h, w, 1]
        box2 (list): [x, y, w, h], all have the shape [b, na, h, w, 1]
        giou (bool): whether use giou or not, default False
        diou (bool): whether use diou or not, default False
        ciou (bool): whether use ciou or not, default False
        eps (float): epsilon to avoid divide by zero

    Return:
        iou (Tensor): iou of box1 and box1, with the shape [b, na, h, w, 1]
    """
    px1, py1, px2, py2 = box1
    gx1, gy1, gx2, gy2 = box2
    x1 = paddle.maximum(px1, gx1)
    y1 = paddle.maximum(py1, gy1)
    x2 = paddle.minimum(px2, gx2)
    y2 = paddle.minimum(py2, gy2)

    overlap = ((x2 - x1).clip(0)) * ((y2 - y1).clip(0))

    area1 = (px2 - px1) * (py2 - py1)
    area1 = area1.clip(0)

    area2 = (gx2 - gx1) * (gy2 - gy1)
    area2 = area2.clip(0)

    union = area1 + area2 - overlap + eps
    iou = overlap / union

    if giou or ciou or diou:
        # convex w, h
        cw = paddle.maximum(px2, gx2) - paddle.minimum(px1, gx1)
        ch = paddle.maximum(py2, gy2) - paddle.minimum(py1, gy1)
        if giou:
            c_area = cw * ch + eps
            return iou - (c_area - union) / c_area
        else:
            # convex diagonal squared
            c2 = cw**2 + ch**2 + eps
            # center distance
            rho2 = (
                (px1 + px2 - gx1 - gx2)**2 + (py1 + py2 - gy1 - gy2)**2) / 4
            if diou:
                return iou - rho2 / c2
            else:
                w1, h1 = px2 - px1, py2 - py1 + eps
                w2, h2 = gx2 - gx1, gy2 - gy1 + eps
                delta = paddle.atan(w1 / h1) - paddle.atan(w2 / h2)
                v = (4 / math.pi**2) * paddle.pow(delta, 2)
                alpha = v / (1 + eps - iou + v)
                alpha.stop_gradient = True
                return iou - (rho2 / c2 + v * alpha)
    else:
        return iou


def rect2rbox(bboxes):
    """
    :param bboxes: shape (n, 4) (xmin, ymin, xmax, ymax)
    :return: dbboxes: shape (n, 5) (x_ctr, y_ctr, w, h, angle)
    """
    bboxes = bboxes.reshape(-1, 4)
    num_boxes = bboxes.shape[0]

    x_ctr = (bboxes[:, 2] + bboxes[:, 0]) / 2.0
    y_ctr = (bboxes[:, 3] + bboxes[:, 1]) / 2.0
    edges1 = np.abs(bboxes[:, 2] - bboxes[:, 0])
    edges2 = np.abs(bboxes[:, 3] - bboxes[:, 1])
    angles = np.zeros([num_boxes], dtype=bboxes.dtype)

    inds = edges1 < edges2

    rboxes = np.stack((x_ctr, y_ctr, edges1, edges2, angles), axis=1)
    rboxes[inds, 2] = edges2[inds]
    rboxes[inds, 3] = edges1[inds]
    rboxes[inds, 4] = np.pi / 2.0
    return rboxes


def delta2rbox(rrois,
               deltas,
               means=[0, 0, 0, 0, 0],
               stds=[1, 1, 1, 1, 1],
               wh_ratio_clip=1e-6):
    """
    :param rrois: (cx, cy, w, h, theta)
    :param deltas: (dx, dy, dw, dh, dtheta)
    :param means:
    :param stds:
    :param wh_ratio_clip:
    :return:
    """
    means = paddle.to_tensor(means)
    stds = paddle.to_tensor(stds)
    deltas = paddle.reshape(deltas, [-1, deltas.shape[-1]])
    denorm_deltas = deltas * stds + means

    dx = denorm_deltas[:, 0]
    dy = denorm_deltas[:, 1]
    dw = denorm_deltas[:, 2]
    dh = denorm_deltas[:, 3]
    dangle = denorm_deltas[:, 4]

    max_ratio = np.abs(np.log(wh_ratio_clip))
    dw = paddle.clip(dw, min=-max_ratio, max=max_ratio)
    dh = paddle.clip(dh, min=-max_ratio, max=max_ratio)

    rroi_x = rrois[:, 0]
    rroi_y = rrois[:, 1]
    rroi_w = rrois[:, 2]
    rroi_h = rrois[:, 3]
    rroi_angle = rrois[:, 4]

    gx = dx * rroi_w * paddle.cos(rroi_angle) - dy * rroi_h * paddle.sin(
        rroi_angle) + rroi_x
    gy = dx * rroi_w * paddle.sin(rroi_angle) + dy * rroi_h * paddle.cos(
        rroi_angle) + rroi_y
    gw = rroi_w * dw.exp()
    gh = rroi_h * dh.exp()
    ga = np.pi * dangle + rroi_angle
    ga = (ga + np.pi / 4) % np.pi - np.pi / 4
    ga = paddle.to_tensor(ga)

    gw = paddle.to_tensor(gw, dtype='float32')
    gh = paddle.to_tensor(gh, dtype='float32')
    bboxes = paddle.stack([gx, gy, gw, gh, ga], axis=-1)
    return bboxes


def rbox2delta(proposals, gt, means=[0, 0, 0, 0, 0], stds=[1, 1, 1, 1, 1]):
    """

    Args:
        proposals:
        gt:
        means: 1x5
        stds: 1x5

    Returns:

    """
    proposals = proposals.astype(np.float64)

    PI = np.pi

    gt_widths = gt[..., 2]
    gt_heights = gt[..., 3]
    gt_angle = gt[..., 4]

    proposals_widths = proposals[..., 2]
    proposals_heights = proposals[..., 3]
    proposals_angle = proposals[..., 4]

    coord = gt[..., 0:2] - proposals[..., 0:2]
    dx = (np.cos(proposals[..., 4]) * coord[..., 0] + np.sin(proposals[..., 4])
          * coord[..., 1]) / proposals_widths
    dy = (-np.sin(proposals[..., 4]) * coord[..., 0] +
          np.cos(proposals[..., 4]) * coord[..., 1]) / proposals_heights
    dw = np.log(gt_widths / proposals_widths)
    dh = np.log(gt_heights / proposals_heights)
    da = (gt_angle - proposals_angle)

    da = (da + PI / 4) % PI - PI / 4
    da /= PI

    deltas = np.stack([dx, dy, dw, dh, da], axis=-1)
    means = np.array(means, dtype=deltas.dtype)
    stds = np.array(stds, dtype=deltas.dtype)
    deltas = (deltas - means) / stds
    deltas = deltas.astype(np.float32)
    return deltas


def bbox_decode(bbox_preds,
                anchors,
                means=[0, 0, 0, 0, 0],
                stds=[1, 1, 1, 1, 1]):
    """decode bbox from deltas
    Args:
        bbox_preds: [N,H,W,5]
        anchors: [H*W,5]
    return:
        bboxes: [N,H,W,5]
    """
    means = paddle.to_tensor(means)
    stds = paddle.to_tensor(stds)
    num_imgs, H, W, _ = bbox_preds.shape
    bboxes_list = []
    for img_id in range(num_imgs):
        bbox_pred = bbox_preds[img_id]
        # bbox_pred.shape=[5,H,W]
        bbox_delta = bbox_pred
        anchors = paddle.to_tensor(anchors)
        bboxes = delta2rbox(
            anchors, bbox_delta, means, stds, wh_ratio_clip=1e-6)
        bboxes = paddle.reshape(bboxes, [H, W, 5])
        bboxes_list.append(bboxes)
    return paddle.stack(bboxes_list, axis=0)


def poly2rbox(polys):
    """
    poly:[x0,y0,x1,y1,x2,y2,x3,y3]
    to
    rotated_boxes:[x_ctr,y_ctr,w,h,angle]
    """
    rotated_boxes = []
    for poly in polys:
        poly = np.array(poly[:8], dtype=np.float32)

        pt1 = (poly[0], poly[1])
        pt2 = (poly[2], poly[3])
        pt3 = (poly[4], poly[5])
        pt4 = (poly[6], poly[7])

        edge1 = np.sqrt((pt1[0] - pt2[0]) * (pt1[0] - pt2[0]) + (pt1[1] - pt2[
            1]) * (pt1[1] - pt2[1]))
        edge2 = np.sqrt((pt2[0] - pt3[0]) * (pt2[0] - pt3[0]) + (pt2[1] - pt3[
            1]) * (pt2[1] - pt3[1]))

        width = max(edge1, edge2)
        height = min(edge1, edge2)

        rbox_angle = 0
        if edge1 > edge2:
            rbox_angle = np.arctan2(
                float(pt2[1] - pt1[1]), float(pt2[0] - pt1[0]))
        elif edge2 >= edge1:
            rbox_angle = np.arctan2(
                float(pt4[1] - pt1[1]), float(pt4[0] - pt1[0]))

        def norm_angle(angle, range=[-np.pi / 4, np.pi]):
            return (angle - range[0]) % range[1] + range[0]

        rbox_angle = norm_angle(rbox_angle)

        x_ctr = float(pt1[0] + pt3[0]) / 2
        y_ctr = float(pt1[1] + pt3[1]) / 2
        rotated_box = np.array([x_ctr, y_ctr, width, height, rbox_angle])
        rotated_boxes.append(rotated_box)
    ret_rotated_boxes = np.array(rotated_boxes)
    assert ret_rotated_boxes.shape[1] == 5
    return ret_rotated_boxes


def cal_line_length(point1, point2):
    import math
    return math.sqrt(
        math.pow(point1[0] - point2[0], 2) + math.pow(point1[1] - point2[1],
                                                      2))


def get_best_begin_point_single(coordinate):
    x1, y1, x2, y2, x3, y3, x4, y4 = coordinate
    xmin = min(x1, x2, x3, x4)
    ymin = min(y1, y2, y3, y4)
    xmax = max(x1, x2, x3, x4)
    ymax = max(y1, y2, y3, y4)
    combinate = [[[x1, y1], [x2, y2], [x3, y3], [x4, y4]],
                 [[x4, y4], [x1, y1], [x2, y2], [x3, y3]],
                 [[x3, y3], [x4, y4], [x1, y1], [x2, y2]],
                 [[x2, y2], [x3, y3], [x4, y4], [x1, y1]]]
    dst_coordinate = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]
    force = 100000000.0
    force_flag = 0
    for i in range(4):
        temp_force = cal_line_length(combinate[i][0], dst_coordinate[0]) \
                     + cal_line_length(combinate[i][1], dst_coordinate[1]) \
                     + cal_line_length(combinate[i][2], dst_coordinate[2]) \
                     + cal_line_length(combinate[i][3], dst_coordinate[3])
        if temp_force < force:
            force = temp_force
            force_flag = i
    if force_flag != 0:
        pass
    return np.array(combinate[force_flag]).reshape(8)


def rbox2poly_np(rrects):
    """
    rrect:[x_ctr,y_ctr,w,h,angle]
    to
    poly:[x0,y0,x1,y1,x2,y2,x3,y3]
    """
    polys = []
    for i in range(rrects.shape[0]):
        rrect = rrects[i]
        # x_ctr, y_ctr, width, height, angle = rrect[:5]
        x_ctr = rrect[0]
        y_ctr = rrect[1]
        width = rrect[2]
        height = rrect[3]
        angle = rrect[4]
        tl_x, tl_y, br_x, br_y = -width / 2, -height / 2, width / 2, height / 2
        rect = np.array([[tl_x, br_x, br_x, tl_x], [tl_y, tl_y, br_y, br_y]])
        R = np.array([[np.cos(angle), -np.sin(angle)],
                      [np.sin(angle), np.cos(angle)]])
        poly = R.dot(rect)
        x0, x1, x2, x3 = poly[0, :4] + x_ctr
        y0, y1, y2, y3 = poly[1, :4] + y_ctr
        poly = np.array([x0, y0, x1, y1, x2, y2, x3, y3], dtype=np.float32)
        poly = get_best_begin_point_single(poly)
        polys.append(poly)
    polys = np.array(polys)
    return polys


def rbox2poly(rrects):
    """
    rrect:[x_ctr,y_ctr,w,h,angle]
    to
    poly:[x0,y0,x1,y1,x2,y2,x3,y3]
    """
    N = paddle.shape(rrects)[0]

    x_ctr = rrects[:, 0]
    y_ctr = rrects[:, 1]
    width = rrects[:, 2]
    height = rrects[:, 3]
    angle = rrects[:, 4]

    tl_x, tl_y, br_x, br_y = -width * 0.5, -height * 0.5, width * 0.5, height * 0.5

    normal_rects = paddle.stack(
        [tl_x, br_x, br_x, tl_x, tl_y, tl_y, br_y, br_y], axis=0)
    normal_rects = paddle.reshape(normal_rects, [2, 4, N])
    normal_rects = paddle.transpose(normal_rects, [2, 0, 1])

    sin, cos = paddle.sin(angle), paddle.cos(angle)
    # M.shape=[N,2,2]
    M = paddle.stack([cos, -sin, sin, cos], axis=0)
    M = paddle.reshape(M, [2, 2, N])
    M = paddle.transpose(M, [2, 0, 1])

    # polys:[N,8]
    polys = paddle.matmul(M, normal_rects)
    polys = paddle.transpose(polys, [2, 1, 0])
    polys = paddle.reshape(polys, [-1, N])
    polys = paddle.transpose(polys, [1, 0])

    tmp = paddle.stack(
        [x_ctr, y_ctr, x_ctr, y_ctr, x_ctr, y_ctr, x_ctr, y_ctr], axis=1)
    polys = polys + tmp
    return polys


def bbox_iou_np_expand(box1, box2, x1y1x2y2=True, eps=1e-16):
    """
    Calculate the iou of box1 and box2 with numpy.

    Args:
        box1 (ndarray): [N, 4]
        box2 (ndarray): [M, 4], usually N != M
        x1y1x2y2 (bool): whether in x1y1x2y2 stype, default True
        eps (float): epsilon to avoid divide by zero
    Return:
        iou (ndarray): iou of box1 and box2, [N, M]
    """
    N, M = len(box1), len(box2)  # usually N != M
    if x1y1x2y2:
        b1_x1, b1_y1 = box1[:, 0], box1[:, 1]
        b1_x2, b1_y2 = box1[:, 2], box1[:, 3]
        b2_x1, b2_y1 = box2[:, 0], box2[:, 1]
        b2_x2, b2_y2 = box2[:, 2], box2[:, 3]
    else:
        # cxcywh style
        # Transform from center and width to exact coordinates
        b1_x1, b1_x2 = box1[:, 0] - box1[:, 2] / 2, box1[:, 0] + box1[:, 2] / 2
        b1_y1, b1_y2 = box1[:, 1] - box1[:, 3] / 2, box1[:, 1] + box1[:, 3] / 2
        b2_x1, b2_x2 = box2[:, 0] - box2[:, 2] / 2, box2[:, 0] + box2[:, 2] / 2
        b2_y1, b2_y2 = box2[:, 1] - box2[:, 3] / 2, box2[:, 1] + box2[:, 3] / 2

    # get the coordinates of the intersection rectangle
    inter_rect_x1 = np.zeros((N, M), dtype=np.float32)
    inter_rect_y1 = np.zeros((N, M), dtype=np.float32)
    inter_rect_x2 = np.zeros((N, M), dtype=np.float32)
    inter_rect_y2 = np.zeros((N, M), dtype=np.float32)
    for i in range(len(box2)):
        inter_rect_x1[:, i] = np.maximum(b1_x1, b2_x1[i])
        inter_rect_y1[:, i] = np.maximum(b1_y1, b2_y1[i])
        inter_rect_x2[:, i] = np.minimum(b1_x2, b2_x2[i])
        inter_rect_y2[:, i] = np.minimum(b1_y2, b2_y2[i])
    # Intersection area
    inter_area = np.maximum(inter_rect_x2 - inter_rect_x1, 0) * np.maximum(
        inter_rect_y2 - inter_rect_y1, 0)
    # Union Area
    b1_area = np.repeat(
        ((b1_x2 - b1_x1) * (b1_y2 - b1_y1)).reshape(-1, 1), M, axis=-1)
    b2_area = np.repeat(
        ((b2_x2 - b2_x1) * (b2_y2 - b2_y1)).reshape(1, -1), N, axis=0)

    ious = inter_area / (b1_area + b2_area - inter_area + eps)
    return ious


def bbox2distance(points, bbox, max_dis=None, eps=0.1):
    """Decode bounding box based on distances.
    Args:
        points (Tensor): Shape (n, 2), [x, y].
        bbox (Tensor): Shape (n, 4), "xyxy" format
        max_dis (float): Upper bound of the distance.
        eps (float): a small value to ensure target < max_dis, instead <=
    Returns:
        Tensor: Decoded distances.
    """
    left = points[:, 0] - bbox[:, 0]
    top = points[:, 1] - bbox[:, 1]
    right = bbox[:, 2] - points[:, 0]
    bottom = bbox[:, 3] - points[:, 1]
    if max_dis is not None:
        left = left.clip(min=0, max=max_dis - eps)
        top = top.clip(min=0, max=max_dis - eps)
        right = right.clip(min=0, max=max_dis - eps)
        bottom = bottom.clip(min=0, max=max_dis - eps)
    return paddle.stack([left, top, right, bottom], -1)


def distance2bbox(points, distance, max_shape=None):
    """Decode distance prediction to bounding box.
        Args:
            points (Tensor): Shape (n, 2), [x, y].
            distance (Tensor): Distance from the given point to 4
                boundaries (left, top, right, bottom).
            max_shape (tuple): Shape of the image.
        Returns:
            Tensor: Decoded bboxes.
        """
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    if max_shape is not None:
        x1 = x1.clip(min=0, max=max_shape[1])
        y1 = y1.clip(min=0, max=max_shape[0])
        x2 = x2.clip(min=0, max=max_shape[1])
        y2 = y2.clip(min=0, max=max_shape[0])
    return paddle.stack([x1, y1, x2, y2], -1)


def bbox_center(boxes):
    """Get bbox centers from boxes.
    Args:
        boxes (Tensor): boxes with shape (N, 4), "xmin, ymin, xmax, ymax" format.
    Returns:
        Tensor: boxes centers with shape (N, 2), "cx, cy" format.
    """
    boxes_cx = (boxes[..., 0] + boxes[..., 2]) / 2
    boxes_cy = (boxes[..., 1] + boxes[..., 3]) / 2
    return paddle.stack([boxes_cx, boxes_cy], axis=-1)


def batch_distance2bbox(points, distance, max_shapes=None):
    """Decode distance prediction to bounding box for batch.
    Args:
        points (Tensor): [B, ..., 2]
        distance (Tensor): [B, ..., 4]
        max_shapes (tuple): [B, 2], "h,w" format, Shape of the image.
    Returns:
        Tensor: Decoded bboxes.
    """
    x1 = points[..., 0] - distance[..., 0]
    y1 = points[..., 1] - distance[..., 1]
    x2 = points[..., 0] + distance[..., 2]
    y2 = points[..., 1] + distance[..., 3]
    if max_shapes is not None:
        for i, max_shape in enumerate(max_shapes):
            x1[i] = x1[i].clip(min=0, max=max_shape[1])
            y1[i] = y1[i].clip(min=0, max=max_shape[0])
            x2[i] = x2[i].clip(min=0, max=max_shape[1])
            y2[i] = y2[i].clip(min=0, max=max_shape[0])
    return paddle.stack([x1, y1, x2, y2], -1)
