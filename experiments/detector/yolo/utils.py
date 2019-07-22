import torch
import cv2
import numpy as np

from matplotlib import pyplot as plt
from matplotlib import cm as colormap
from matplotlib.colors import Normalize

def parse_cfg(file):
    '''
    Parses the original cfg file
    
    Argument
    --------
    file: str
        A path to cfg file.
        
    Output
    ------
    layers: list
        A list of dicts with config for each layer. 
        Note: the 0th element of the list contain config for network itself
    '''
    
    layers = []
    layer = {}

    with open(file, 'r') as readf:
        lines = readf.read().split('\n')
        # skip commented lines
        lines = [line for line in lines if not line.startswith('#')]
        # skip empty lines
        lines = [line for line in lines if not len(line) == 0]
        # remove all whitespaces
        lines = [line.replace(' ', '') for line in lines]

        for line in lines:

            # if the name of the layer (they are of form : [*])
            if line.startswith('[') and line.endswith(']'):

                # save the prev. layer as the next lines contains info for the next layer
                if len(layer) > 0:
                    layers.append(layer)
                    layer = {}

                # add the layer's name/type
                layer['name'] = line.replace('[', '').replace(']', '')

            # if not the name then parse agruments
            else:
                # all arguments follows the pattern: 'key=value'
                key, value = line.split('=')
                # add info to the layer
                layer[key] = value

        # append the last layer 
        layers.append(layer)
        
    return layers

def get_center_coords(bboxes): #top_left_x, top_left_y, box_w, box_h
    '''todo: comment on generality'''
    bboxes[:, 0] = bboxes[:, 0] + bboxes[:, 2] // 2
    bboxes[:, 1] = bboxes[:, 1] + bboxes[:, 3] // 2
    return bboxes

def get_corner_coords(bboxes):
    '''
    Transforms the bounding boxes coordinate from (cx, cy, w, h) into
    (top_left_x, top_left_y, bottom_right_x, bottom_right_y), 
    i.e. into corner coordinates.
    
    Argument
    --------
    bboxes: torch.FloatTensor
        A tensor of size (P, D) where D should contain info about the coords
        in the following order (center_x, center_y, width, height). Note:
        D can be higher than 4.
    
    Outputs
    -------
    top_left_x, top_left_y, bottom_right_x, bottom_right_y: torch.FloatTensors
        Transformed coordinates for bboxes: top-left corner coordinates for x and y
        and bottom-right coordinates for x and y respectively.
    '''
    top_left_x = bboxes[:, 0] - bboxes[:, 2]/2
    top_left_y = bboxes[:, 1] - bboxes[:, 3]/2
    bottom_right_x = bboxes[:, 0] + bboxes[:, 2]/2
    bottom_right_y = bboxes[:, 1] + bboxes[:, 3]/2

    return top_left_x, top_left_y, bottom_right_x, bottom_right_y

def iou_vectorized(bboxes1, bboxes2, without_center_coords=False):
    '''
    Calculates intersection over union between every bbox in bboxes1 with
    every bbox in bboxes2, i.e. Cartesian product of both sets.
    
    Arguments
    ---------
    bboxes1: torch.FloatTensor
        (M, 4 + *) shapped tensor with M bboxes with 4 bbox coordinates (cx, cy, w, h, *).
    bboxes2: torch.FloatTensor
        (N, 4 + *) shapped tensor with M bboxes with 4 bbox coordinates (cx, cy, w, h, *).
    without_center_coords: bool
        True: IoU is calculated only using width and height (no center coordinates).
        It is useful on training when the best bbox is selected to replace the gt bbox.
        Note: bboxes1 and bboxes2 are expected to have (M, 2 + *) and (N, 2 + *), respectively.
        
    Output
    ------
    : torch.FloatTensor
        (M, N) shapped tensor with (i, j) corresponding to IoU between i-th bbox 
        from bboxes1 with j-th bbox from bboxes2.
    '''
    # pixel shift is 0 if we calculate without center coordinates and 1 otherwise.
    # Why? Let's say I want to calculate the number of pixels the width of a box 
    # overlaps given two x coordinates for pixels: 0 and 5. So, the side is 6 pixels
    # but 5 - 0 = 5. Therefore, we add 1. 
    # However, we don't need to do it when we don't have center coordinates
    # i.e. without_center_coords = True
    px_shift = 1
    
    # add 'fake' center coordinates. You can use any value, we use zeros
    if without_center_coords:
        bboxes1 = torch.cat([torch.zeros_like(bboxes1[:, :2]), bboxes1], dim=1)
        bboxes2 = torch.cat([torch.zeros_like(bboxes2[:, :2]), bboxes2], dim=1)
        px_shift = 0
    
    M, D = bboxes1.shape
    N, D = bboxes2.shape
    
    # Transform coords of the 1st bboxes (y=0 is at the top, and increases downwards)
    top_left_x1, top_left_y1, bottom_right_x1, bottom_right_y1 = get_corner_coords(bboxes1)
    # Transform coords of the 2nd bboxes
    top_left_x2, top_left_y2, bottom_right_x2, bottom_right_y2 = get_corner_coords(bboxes2)

    # broadcasting 1st bboxes
    top_left_x1 = top_left_x1.view(M, 1)
    top_left_y1 = top_left_y1.view(M, 1)
    bottom_right_x1 = bottom_right_x1.view(M, 1)
    bottom_right_y1 = bottom_right_y1.view(M, 1)
    # broadcasting 2nd bboxes
    top_left_x2 = top_left_x2.view(1, N)
    top_left_y2 = top_left_y2.view(1, N)
    bottom_right_x2 = bottom_right_x2.view(1, N)
    bottom_right_y2 = bottom_right_y2.view(1, N)
    
    # calculate coords for intersection
    inner_top_left_x = torch.max(top_left_x1, top_left_x2)
    inner_top_left_y = torch.max(top_left_y1, top_left_y2)
    inner_bottom_right_x = torch.min(bottom_right_x1, bottom_right_x2)
    inner_bottom_right_y = torch.min(bottom_right_y1, bottom_right_y2)
    
    # area = side_a * side_b
    # clamp(x, min=0) = max(x, 0)
    # we make sure that the area is 0 if size of a side is negative
    # which means that inner_top_left_x > inner_bottom_right_x which is not feasible
    # Note: adding one because the coordinates starts at 0 and let's
    a = torch.clamp(inner_bottom_right_x - inner_top_left_x + px_shift, min=0)
    b = torch.clamp(inner_bottom_right_y - inner_top_left_y + px_shift, min=0)
    inner_area = a * b

    # finally we calculate union for each pair of bboxes
    out_area1 = (bottom_right_x1 - top_left_x1 + px_shift) * (bottom_right_y1 - top_left_y1 + px_shift)
    out_area2 = (bottom_right_x2 - top_left_x2 + px_shift) * (bottom_right_y2 - top_left_y2 + px_shift)
    out_area = out_area1 + out_area2 - inner_area

    return inner_area / out_area

def objectness_filter_and_nms(predictions, classes, obj_thresh=0.8, nms_thresh=0.4):
    '''
    Performs filtering according objectness score and non-maximum supression on predictions.
    
    Arguments
    ---------
    predictions: torch.FloatTensor
        A tensor of size (B, P, 5+classes) with predictions.
        B -- batch size; P -- number of predictions for an image, 
        i.e. 3 scales and 3 anchor boxes and
        For example: P = (13*13 + 26*26 + 52*52) * 3 = 10647;
        5 + classes -- (cx, cy, w, h, obj_score, {prob_class}).
    classes: int
        An integer with the number of classes to detect.
    obj_thresh: float
        A float that corresponds to the lowest objectness score the detector allows.
    nms_thresh: float
        Corresponds to the highest IoU the detector allows.
        
    Output
    ------
    predictions: torch.FloatTensor or None
        Predictions after objectness filtering and non-max supression (same size
        as predictions in arguments but with a different P). Returns None when
        there no detections found.
    '''
    
    # iterate for images in a batch
    for i, prediction in enumerate(predictions):
        ## objectness thresholding
        
        # If prediction's (bbox') score is higher than obj_thress keep the prediction
        # the fourth (fifth) element is objectness score; if there are no
        # detections with obj score higher than obj_thresh, return None
        objectness_mask = (prediction[:, 4] > obj_thresh)
        
        if len(torch.nonzero(objectness_mask)) == 0:
            return None
        
        prediction = prediction[objectness_mask]

        # if no object on an image found, continue with the next image
        if prediction.size(0) == 0:
            continue
        
        ## non-max supression
        # The idea is as follows. If a prediction "survived" objectness filtering
        # then it is considered meaningful. Since we may have multiple detections of 
        # one object on an image we need to filter out those predictions that have
        # substantial (more than nms_thresh) overlap, or IoU, with the box with highest
        # class score. Also note that one image might contact more than object of the same class.
        # So, as they don't have high IoU with the box with highest class score, they will be kept
        # in the list of predictions
        
        # for each prediction we save the class with the maximum class score
        pred_score, pred_classes = torch.max(prediction[:, 5:5+classes], dim=1)
        
        # we are going to iterate through classes, so, first, we select the set of unique classes
        unique_classes = pred_classes.unique().float()
        
        # initialize the list of filtered detections
        detections_after_nms = []

        for cls in unique_classes:
            # select only the entries for a specific class.
            # pred_classes is of torch.LongTensor type but we need torch.FloatTensor
            prediction_4_cls = prediction[pred_classes.float() == cls]
            # then we sort predictions for a specific class by objectness score (high -> low)
            sort_pred_idxs = torch.sort(prediction_4_cls[:, 4], descending=True)[1]
            prediction_4_cls = prediction_4_cls[sort_pred_idxs]
            
            # next we want to fill out detections_after_nms with only with those objects
            # that has a unique position, i.e. low IoU with other predictions.
            # The idea here is to append (save) the first prediction in the prediction_4_cls
            # and calculate IoUs with the rest predictions in that prediction_4_cls of the 
            # ordered list. Next, the predictions with the high IoU
            # with the first prediction in prediction_4_cls will be discarded. 
            # For the next iteration, the first prediction will be the prediction with
            # the highest obj score among the ones that are left.
            # exit the loop when there is no prediction left after the nms
            while len(prediction_4_cls) > 0:
                # we append the first prediction for a specific class to the list of predictions.
                # We can do this because we ordered the prediction_4_cls beforehand.
                detections_after_nms.append(prediction_4_cls[0].unsqueeze(0))
                
                # also stop when this is the last prediction in prediction_4_cls
                if len(prediction_4_cls) == 1:
                    break
                
                # calculate IoUs with the first pred in prediction_4_cls and the rest of them
                ious = iou_vectorized(prediction_4_cls[0, :5].unsqueeze(0), prediction_4_cls[1:, :5])
                # when iou_vectorized inputs two tensors, the ious.shape is (N, M) but now N = 1
                # and [ious < nms_thresh] should be one dimesional
                ious = ious.reshape(-1)
                # filter out the first prediction (1:) and the ones with high IoU with the 0th pred
                prediction_4_cls = prediction_4_cls[1:][ious < nms_thresh]
        
        # as detections_after_nms is a list, we concatenate its elements to a tensor
        predictions = torch.cat(detections_after_nms)

    return predictions

def scale_numbers(num1, num2, largest_num_target):
    '''
    Scales two numbers (for example, dimensions) keeping aspect ratio.
    
    Arguments
    ---------
    num1: float or int
        The 1st number (dim1).
    num2: float or int
        The 2nd number (dim2).
    largest_num_target: int
        The expected size of the largest number among 1st and 2nd numbers.
        
    Outputs
    -------
    : (int, int, float)
        Two scaled numbers such that the largest is equal to largest_num_target 
        maintaining the same aspect ratio as num1 and num2 in input. Also,
        returns a scalling coefficient.
        Note: two ints are returned.
        
    Examples
    --------
        scale_numbers(832, 832, 416) -> (416, 416, 0.5)
        scale_numbers(223, 111, 416) -> (416, 207, 1.865...)
        scale_numbers(100, 200, 416) -> (208, 416, 2.08)
        scale_numbers(200, 832, 416) -> (100, 416, 0.5)
    '''
    # make sure the arguments are of correct types
    assert isinstance(largest_num_target, int), 'largest_num_target should be "int"'
    
    # to make the largest number to be equal largest_num_target keeping aspect ratio
    # we need, first, to estimate by how much the largest number is smaller (larger) 
    # than largest_num_target and, second, to scale both numbers by this ratio.
    
    # select the maximum among two numbers
    max_num = max(num1, num2)
    # calculate scalling coefficient
    scale_coeff = largest_num_target / max_num
    # scale both numbers
    num1 = num1 * scale_coeff
    num2 = num2 * scale_coeff
    
    # making sure that the numbers has int type
    return int(num1), int(num2), scale_coeff

# def letterbox_pad(img, net_input_size, color=(127.5, 127.5, 127.5)):
def letterbox_pad(img, color=(127.5, 127.5, 127.5)):    
    '''
    Adds padding to an image according to the original implementation of darknet.
    Specifically, it will pad the image up to (net_input_size x net_input_size) size.
    
    Arguments
    ---------
    img: numpy.ndarray
        An image to pad.
#    net_input_size: int
#        The network's input size.
    color: (float or int, float or int, float or int) \in [0, 255]
        The RGB intensities. The image will be padded with this color.
        
    Output
    ------
    img: numpy.ndarray
        The padded image.
    pad_sizes: (int, int, int, int)
        The sizes of paddings. Used in show_prediction module where we need to shift
        predictions by the size of the padding. order: top, bottom, left, right
    '''
    # make sure the arguments are of correct types
    assert isinstance(img, np.ndarray), '"img" should have numpy.ndarray type'
#     assert isinstance(net_input_size, int), '"net_input_size" should have int type'
    assert (
        isinstance(color[0], (int, float)) and 
        isinstance(color[1], (int, float)) and 
        isinstance(color[2], (int, float))
    ), 'each element of "color" should contain either a float or an int'
    
    H, W, C = img.shape
    max_side_len = max(H, W)
    
    # if width is higher than height then, to make a squared-shaped image, we need
    # to pad the height; else, we need to pad width.
    if W > H:
        # calculates how much should be padded "on top" which is a half of 
        # the difference between the target size and the current height
        pad_top = (max_side_len - H) // 2
        # another half is added to the bottom
        pad_bottom = max_side_len - (H + pad_top)
        pad_left = 0
        pad_right = 0
        
    else:
        pad_top = 0
        pad_bottom = 0
        # calculates how much should be padded "on left" which is a half of 
        # the difference between the target size and the current width
        pad_left = (max_side_len - W) // 2
        pad_right = max_side_len - (W + pad_left)
    
    # pad_widths should contain three pairs (because of 3d) of padding sizes:
    # first pair adds rows [from top and bottom], 
    # second adds columns [from left to right],
    # the third adds nothing because we pad only spatially, not channel-wise
    pad_widths = [[pad_top, pad_bottom], [pad_left, pad_right], [0, 0]]
    # for each padding we specify a color (constant parameter)
    color = [[color, color], [color, color], [0, 0]]
    # perform padding
    img = np.pad(img, pad_widths, 'constant', constant_values=color)
    # save padding sizes
    pad_sizes = (pad_top, pad_bottom, pad_left, pad_right)
    
    return img, pad_sizes
    
    
def predict_and_save(img_path, save_path, model, device, labels_path='./data/coco.names', show=False):
    '''
    Predicts objects on an image, draws the bounding boxes around the predicted objects,
    and saves the image.
    
    Arguments
    ---------
    image_path: str
        The path to an image with objects to predict.
    save_path: str
        The path for the output image with detected objects.
    model: Darknet
        The model which will be used for inference.
    device: torch.device or str
        Device for calculations.
    labels_path: str
        The path to the object names.
    show: bool
        Whether to show the output image with bounding boxes, for example, in jupyter notebook
        
    Outputs
    -------
    prediction: torch.FloatTensor or NoneType
        Predictions of a size (<number of detected objects>, 4+1+<number of classes>). 
        prediction is NoneType when no object has been detected on an image.
    
    img_raw: numpy.ndarray
        A resulting image with bounding objects drawn on it.
    '''
    # make sure the arguments are of correct types
    assert isinstance(img_path, str), '"img_path" should be str'
    assert save_path is None or isinstance(save_path, str), 'save_path should be NoneType or str'
    assert isinstance(labels_path, str), '"labels_path" should be str'
#     assert isinstance(model, darknet.Darknet), 'model should be a Darknet module'
    assert isinstance(device, (torch.device, str)), 'device should be either torch.device or str'
    assert isinstance(show, bool), 'show should be boolean'

    # parameters of the vizualization: color palette, figsize to show, 
    # label parameters, jpeg quality
    norm = Normalize(vmin=0, vmax=model.classes)
    color_map = colormap.tab10
    figsize = (15, 15)
    line_thickness = 2
    font_face = cv2.FONT_HERSHEY_PLAIN
    font_scale = 1.1
    font_color = [255, 255, 255] # white
    font_thickness = 1
    jpg_quality = 80

    # make a dict: {class_number: class_name}
    names = [name.replace('\n', '') for name in open(labels_path, 'r').readlines()]
    num2name = {num: name for num, name in enumerate(names)}

    # read an image and transform the colors from BGR to RGB
    img_raw = cv2.imread(img_path)
    img_raw = cv2.cvtColor(img_raw, cv2.COLOR_BGR2RGB)

    # add letterbox padding and save the pad sizes and scalling coefficient
    # to use it latter when drawing bboxes on the original image
    H, W, C = img_raw.shape
    H_new, W_new, scale = scale_numbers(H, W, model.model_width)
    img = cv2.resize(img_raw, (W_new, H_new))
    img, pad_sizes = letterbox_pad(img)

    # HWC -> CHW, scale intensities to [0, 1], send to pytorch, add 'batch-'dimension
    img = img.transpose((2, 0, 1))
    img = img / 255
    img = torch.from_numpy(img).float()
    img = img.unsqueeze(0)

    # make prediction
    prediction = model(img, device=device)
    # and apply objectness filtering and nms. If returns None, draw a box that states it
    prediction = objectness_filter_and_nms(prediction, model.classes) # todo check whether it has batch dim
    

    ### if no objects have been detected draw one rectangle on the perimeter of the 
    # img_raw with text that no objects are found. for comments for this if condition 
    # please see the for-loop below
    if prediction is None:
        font_scale = 2
        font_thickness = 2
        top_left_coords = (0, 0)
        bbox_color = (0, 0, 0)
        font_color = (255, 255, 255)
        text = 'No objects found :-('
        text_size = cv2.getTextSize(text, font_face, font_scale, font_thickness)[0]
        bottom_right_coords_ = top_left_coords[0] + text_size[0] + 12, top_left_coords[1] + text_size[1] + 12
        xy_position = (2, 5 + text_size[1])
        cv2.rectangle(img_raw, top_left_coords, bottom_right_coords_, bbox_color, cv2.FILLED)
        cv2.putText(img_raw, text, xy_position, font_face, font_scale, font_color, font_thickness)
        if show:
            plt.imshow(img_raw)
        img_raw = cv2.cvtColor(img_raw, cv2.COLOR_RGB2BGR)
        cv2.imwrite(save_path, img_raw, [cv2.IMWRITE_JPEG_QUALITY, jpg_quality])

        return None, img_raw
    ###

    # since the predictions are made for a resized and padded images, 
    # the bounding boxes have to be scaled and shifted back
    # for that, we shift and scale back the bboxes' attributes
    pad_top, pad_bottom, pad_left, pad_right = pad_sizes
    prediction[:, 0] = (prediction[:, 0] - pad_left) / scale
    prediction[:, 1] = (prediction[:, 1] - pad_top) / scale
    prediction[:, 2] = prediction[:, 2] / scale
    prediction[:, 3] = prediction[:, 3] / scale

    # the, transform the coordinates (cx, cy, w, h) into corner coordinates: 
    # (top_left_x, top_left_y, bottom_right_x, bottom_right_y)
    top_left_x, top_left_y, bottom_right_x, bottom_right_y = get_corner_coords(prediction)

    # detach values from the computation graph, take the int part and transform to np.ndarray
    top_left_x = top_left_x.detach().int().numpy()
    top_left_y = top_left_y.detach().int().numpy()
    bottom_right_x = bottom_right_x.detach().int().numpy()
    bottom_right_y = bottom_right_y.detach().int().numpy()

    # if show initialize a figure environment
    if show:
        plt.figure(figsize=figsize)

    # add each prediction on the image and captures it with a class number
    for i in range(len(prediction)):

        ## ADD BBOXES
        # first we need to extract coords for both top left and bottom right corners
        # note: sometimes, the corner coordinates lie outside of the image itself
        # hence we need to keep them on image -> min and max
        top_left_coords = max(0, top_left_x[i]), max(0, top_left_y[i])
        bottom_right_coords = min(W, bottom_right_x[i]), min(H, bottom_right_y[i])
        # predicted class number
        class_score, class_int = torch.max(prediction[i, 5:5+model.classes], dim=-1) # todo dim (also see NMS with batch dim)
        class_score, class_int = float(class_score), int(class_int)
        
        # select the color for a class according to its label number and scale it to [0, 255]
        bbox_color = color_map(class_int)[:3]
        bbox_color = list(map(lambda x: x * 255, bbox_color))
        # add a bbox
        cv2.rectangle(img_raw, top_left_coords, bottom_right_coords, bbox_color, line_thickness)

        ## ADD A LABLE FOR EACH BBOX INSIDE THE RECTANGLE WITH THE SAME COLOR AS THE BBOX ITSELF
        # predicted class name to put on a bbox
        class_name = num2name[class_int]
        # text to name a box: class name and the probability in percents
        text = f'{class_name} {(class_score * 100):.0f}%'
        # size for the text
        text_size = cv2.getTextSize(text, font_face, font_scale, font_thickness)[0]
        # bottom right coordinates for the small rectangle for the label
        bottom_right_coords_ = top_left_coords[0] + text_size[0] + 4, top_left_coords[1] + text_size[1] + 4
        # adds a small rectangle of the same color to be the background for the label
        cv2.rectangle(img_raw, top_left_coords, bottom_right_coords_, bbox_color, cv2.FILLED)
        # position for text (for min and max comments see calculation of corner coordinates)
        xy_position = max(0, top_left_x[i]) + 2, max(0, top_left_y[i]) + text_size[1]
        # adds the class label with confidence
        cv2.putText(img_raw, text, xy_position, font_face, font_scale, font_color, font_thickness)

    # if show, then, show and close the environment
    if show:
        plt.imshow(img_raw)

    # RGB -> BGR and save output image
    img_raw = cv2.cvtColor(img_raw, cv2.COLOR_RGB2BGR)
    cv2.imwrite(save_path, img_raw, [cv2.IMWRITE_JPEG_QUALITY, jpg_quality])
    
    return prediction, img_raw