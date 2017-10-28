""" Code for application of feature aware normalization to
digital pathology images

Depending on your preference, there are two ways to build the network:

``` python
```

and

``` python
```
"""

from collections import OrderedDict

import lasagne as nn
from lasagne.layers import InputLayer, NonlinearityLayer, BatchNormLayer
from lasagne.layers.dnn import Pool2DDNNLayer as PoolLayer
from lasagne.layers.dnn import Conv2DDNNLayer as ConvLayer
from lasagne.layers import Upscale2DLayer
from lasagne.layers import ExpressionLayer, TransposedConv2DLayer
from lasagne.nonlinearities import rectify, linear
from lasagne.layers import get_output_shape

import layers
from layers import fan_module_simple, get_features, normalize, transpose, fan_module_improved
import pickle
import tools


def build_network(input_var, nb_filter=16, \
                    input_size=(None, 3, tools.INP_PSIZE, tools.INP_PSIZE), \
                    debug_connections=True):
    net = OrderedDict()

    # Input, standardization
    last = net['input'] = InputLayer(input_size, input_var=input_var)
    last = net['norm'] = ExpressionLayer(last, lambda x: normalize(x))

    # load feature encoder
    feats = get_features(last)
    net['features_s8_1'] = feats["conv4_4"]
    net['features_s8_2'] = feats["conv4_1"]
    net['features_s4']   = feats["conv3_3"]

    # Pretrained Encoder as before
    last = net["conv1_1"] = ConvLayer(last, nb_filter, 1, pad=0, flip_filters=False,
                                      nonlinearity=linear)
    last = net["bn1_1"] = layers.NonUpdateBatchNormLayer(last)
    last = net["relu1_1"] = NonlinearityLayer(last, nonlinearity=rectify)
    last = net["conv1_2"] = ConvLayer(last, nb_filter, 1, pad=0, flip_filters=False,
                                      nonlinearity=linear)
    last = net["bn1_2"] = layers.NonUpdateBatchNormLayer(last)
    last = net["relu1_2"] = NonlinearityLayer(last, nonlinearity=rectify)

    # feature aggregation at multiple scales
    last = net["bn1"] = layers.NonUpdateBatchNormLayer(last, beta=None, gamma=None)
    last = fan1 = fan_module_improved(last, net, "s8_1", net['features_s8_1'],
                                        nb_filter=nb_filter, scale=8)
    last = net["bn2"] = layers.NonUpdateBatchNormLayer(last, beta=None, gamma=None)
    last = fan2 = fan_module_improved(last, net, "s8_2", net['features_s8_2'],
                                        nb_filter=nb_filter, scale=8)
    last = net["bn3"] = layers.NonUpdateBatchNormLayer(last, beta=None, gamma=None)
    last = fan3 = fan_module_improved(last, net, "s4", net['features_s4'],
                                        nb_filter=nb_filter, scale=4)
    last = net["bn4"] = layers.FixedBatchNormLayer(last)

    # Decoder as before
    last = net["deconv1_2"] = transpose(last, net["conv1_2"], nonlinearity=None)
    last = net["deconv1_1"] = transpose(last, net["conv1_1"], nonlinearity=None)

    def debug_connection(l):
        l = layers.FixedBatchNormLayer(l, beta=net['bn4'].beta, gamma=net['bn4'].gamma,
                                       mean=net['bn4'].mean, inv_std=net['bn4'].inv_std)
        l = transpose(l, net["conv1_2"], nonlinearity=None, b=net['deconv1_2'].b)
        l = transpose(l, net["conv1_1"], nonlinearity=None, b=net['deconv1_1'].b)

        return l

    debug = []
    if debug_connections:
        debug = [debug_connection(l) for l in [fan1, fan2, fan3]]
    else:
        debug = [net["relu1_2"], fan1, fan2, fan3, net["bn4"]]

    # features and resulting representations
    debug.append(net["s8_1/addition"])
    debug.append(net["s8_1/input_gate"])

    debug.append(net["s8_2/addition"])
    debug.append(net["s8_2/input_gate"])

    debug.append(net["s4/addition"])
    debug.append(net["s4/input_gate"])

    return last, net, debug


def compile_validation(constructor, batch_size=1, psize=300):
    input_var = T.tensor4("input")
    network, layers, debug = build_network(input_var, input_size=(batch_size,3,psize,psize))
    test_prediction = nn.layers.get_output(network, deterministic=True)
    val_fn = theano.function([input_var], test_prediction, allow_input_downcast=True)
    return val_fn, network, layers

class NormalizationNetwork(object):
    """ Inititalizes a new network with a sklearn compatible interface
    """

    def __init__(batch_size = 64,
                 patch_size = tools.INP_PSIZE,
                 fname=None,
                 debug_connections=False):

        assert len(input_size) == 4,\
            "Provide a tuple (N,C,W,H) for input_size"
        assert fname is None or os.path.exists(fname_weights),\
            "Provided file name for weights does not exist"
        self.batch_size = batch_size
        self.patch_size = patch_size
        self.input_size = (batch_size, 3, patch_size, patch_size)
        self.fname_weights = fname

        val_fn, network, layers   = compile_validation()
        self.network       = network
        self.layers        = layers
        self._transform    = val_fn

        self.load_weights(self.fname_weights)

        self.output_shape = nn.layers.get_output_shape(self.network)[2:]

    def fit(self, X, y=None):
        pass

    def transform(self, X):
        normed = []
        normed_imgs = np.zeros((X.shape[0],) + self.output_shape + (3,))
        bsize = self.batch_size

        for i in range(0, X.shape[0], bsize):
            img = dataset[i:i+bsize,...].transpose((0,3,1,2))
            outp = val_fn(img)
            normed_imgs[i:i+bsize,...] = outp.transpose((0,2,3,1))

        return normed_imgs

    def load_weights(self, fname):
        tools.load_weights(self.network, fname)
