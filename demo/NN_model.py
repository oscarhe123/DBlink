# Imports
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable

import matplotlib.pyplot as plt
from DataHandlers import *

# =============================================== Loss Functions ======================================================

def consistency_reg(out_vid):
    '''
    :param out_vid: reconstruction video
    :return: the consistency loss - calculated by the subtraction of each pixel in every frame in its value in the
            previous frame. Average consistency 
    '''
    loss_term = 0
    for i in range(1, out_vid.shape[1]):
        loss_term += torch.sum(torch.abs(out_vid[0, i] - out_vid[0, i-1]))
    return loss_term / out_vid.shape[1]

class TVLoss(nn.Module):
    def __init__(self,TVLoss_weight=1e-4):
        super(TVLoss,self).__init__()
        self.TVLoss_weight = TVLoss_weight

    def forward(self, x):
        batch_size = x.size()[0]
        h_x = x.size()[-2]
        w_x = x.size()[-1]
        count_h = self._tensor_size(x[:, :, :, 1:, :])
        count_w = self._tensor_size(x[:, :, :, :, 1:])
        h_tv = torch.pow((x[:, :, :, 1:, :] - x[:, :, :, :h_x-1, :]), 2).sum()
        w_tv = torch.pow((x[:, :, :, :, 1:] - x[:, :, :, :, :w_x-1]), 2).sum()
        return self.TVLoss_weight * 2 * (h_tv / count_h + w_tv / count_w) / batch_size

    def _tensor_size(self, t):
        return t.size()[1] * t.size()[-2] * t.size()[-1]

# ============================================ This is working fine ===================================================
class ConvOverlapBLSTM(nn.Module):
    def __init__(self, input_size, input_channels, hidden_channels, num_layers, device):
        super(ConvOverlapBLSTM, self).__init__()
        ''' In development'''
        self.input_size = input_size
        self.input_channels = input_channels
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers
        self.device = device
        self.forward_net = ConvLSTM(input_size, input_channels, hidden_channels, kernel_size=(5, 5), num_layers=num_layers, device=device)
        self.reverse_net = ConvLSTM(input_size, input_channels, hidden_channels, kernel_size=(5, 5), num_layers=num_layers, device=device)
        self.conv_net = nn.Sequential(nn.Conv2d(2 * self.hidden_channels, 128, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(128, 256, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(256, 64, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(64, 1, kernel_size=5, padding=2))

    def forward(self, xforward, xreverse):
        """
        xforward, xreverse = B T C H W tensors.
        """

        y_out_fwd, hidden_fwd = self.forward_net(xforward)
        y_out_rev, _ = self.reverse_net(xreverse)

        # Take only the output of the last layer
        y_out_fwd = y_out_fwd[-1]
        y_out_rev = y_out_rev[-1]

        reversed_idx = list(reversed(range(y_out_rev.shape[1])))
        # reverse temporal outputs.
        y_out_rev = y_out_rev[:, reversed_idx, ...]
        ycat = torch.cat((y_out_fwd, y_out_rev), dim=2)

        curr_out = []
        for j in range(ycat.shape[1]):
            curr_out.append(self.conv_net(ycat[:, j]))
        out = torch.stack(curr_out, dim=1)

        return out

class ConvLSTM(nn.Module): #not used 
    def __init__(self, input_size, input_dim, hidden_dim, kernel_size, num_layers, device='cpu',
                 batch_first=False, bias=True, return_all_layers=False):
        super(ConvLSTM, self).__init__()

        self._check_kernel_size_consistency(kernel_size)

        # Make sure that both `kernel_size` and `hidden_dim` are lists having len == num_layers
        kernel_size = self._extend_for_multilayer(kernel_size, num_layers)
        hidden_dim = self._extend_for_multilayer(hidden_dim, num_layers)
        if not len(kernel_size) == len(hidden_dim) == num_layers:
            raise ValueError('Inconsistent list length.')

        self.height, self.width = input_size

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.kernel_size = kernel_size
        self.num_layers = num_layers
        self.device = device
        self.batch_first = batch_first
        self.bias = bias
        self.return_all_layers = return_all_layers
        print('this is useless')
        cell_list = []
        for i in range(0, self.num_layers):
            cur_input_dim = self.input_dim if i == 0 else self.hidden_dim[i-1]

            cell_list.append(ConvLSTMCell(input_size=(self.height, self.width),
                            input_dim=cur_input_dim,
                            hidden_dim=self.hidden_dim[i],
                            kernel_size=self.kernel_size[i],
                            bias=self.bias, device=self.device).to(self.device))

        self.cell_list = nn.ModuleList(cell_list)

    def forward(self, input_tensor, hidden_state=None):
        """
        Parameters
        ----------
        input_tensor:
            5-D Tensor either of shape (t, b, c, h, w) or (b, t, c, h, w)
        Returns
        -------
        last_state_list, layer_output
        """
        self.input_device = input_tensor.device
        # Implement stateful ConvLSTM
        if(hidden_state is None):
            tensor_size = (input_tensor.size(3), input_tensor.size(4))
            hidden_state = self._init_hidden(batch_size=input_tensor.size(0), tensor_size=tensor_size)

        layer_output_list = []
        last_state_list = []

        seq_len = input_tensor.size(1)
        cur_layer_input = input_tensor

        for layer_idx in range(self.num_layers):

            h, c = hidden_state[layer_idx]
            output_inner = []
            for t in range(seq_len):

                h, c = self.cell_list[layer_idx](input_tensor=cur_layer_input[:, t, :, :, :],
                                                 cur_state=[h, c])
                output_inner.append(h)

            layer_output = torch.stack(output_inner, dim=1)
            cur_layer_input = layer_output

            layer_output_list.append(layer_output)
            last_state_list.append([h, c])

        if not self.return_all_layers:
            layer_output_list = layer_output_list[-1:]
            last_state_list = last_state_list[-1:]

        return layer_output_list, last_state_list

    def _init_hidden(self, batch_size, tensor_size):
        init_states = []
        for i in range(self.num_layers):
            init_states.append(self.cell_list[i].init_hidden(batch_size, tensor_size, self.input_device))
        return init_states

    @staticmethod
    def _check_kernel_size_consistency(kernel_size):
        if not (isinstance(kernel_size, tuple) or
                (isinstance(kernel_size, list) and all([isinstance(elem, tuple) for elem in kernel_size]))):
            raise ValueError('`kernel_size` must be tuple or list of tuples')

    @staticmethod
    def _extend_for_multilayer(param, num_layers):
        if not isinstance(param, list):
            param = [param] * num_layers
        return param

class ConvLSTMCell(nn.Module):
    def __init__(self, input_size, input_dim, hidden_dim, kernel_size, bias, device):
        """
        Initialize ConvLSTM cell.
        Parameters
        ----------
        input_size: (int, int)
            Height and width of input tensor as (height, width).
        input_dim: int
            Number of channels of input tensor.
        hidden_dim: int
            Number of channels of hidden state.
        kernel_size: (int, int)
            Size of the convolutional kernel.
        bias: bool
            Whether or not to add the bias.
        device: string
            Specify the device
        """

        super(ConvLSTMCell, self).__init__()

        self.height, self.width = input_size
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim

        self.kernel_size = kernel_size
        self.padding = kernel_size[0] // 2, kernel_size[1] // 2
        self.bias = bias
        self.device = device

        self.conv = nn.Conv2d(in_channels=self.input_dim + self.hidden_dim,
                                out_channels=4 * self.hidden_dim,
                                kernel_size=self.kernel_size,
                                padding=self.padding, bias=self.bias).to(device)

    def forward(self, input_tensor, cur_state):

        h_cur, c_cur = cur_state
        self.conv = self.conv.to(self.device)

        # concatenate along channel axis
        combined = torch.cat([input_tensor, h_cur], dim=1)

        combined_conv = self.conv(combined)  # (in_channel,outchannel) = (inp_dim+hidd_dim , 4*hidden_dim)
        cc_i, cc_f, cc_o, cc_g = torch.split(combined_conv, self.hidden_dim, dim=1)
        i = torch.sigmoid(cc_i)  #input 
        f = torch.sigmoid(cc_f)  #forget
        o = torch.sigmoid(cc_o)  #output 
        g = torch.tanh(cc_g)

        c_next = f * c_cur + i * g
        h_next = o * torch.tanh(c_next)

        return h_next, c_next

    def init_hidden(self, batch_size, tensor_size, device):
        height, width = tensor_size
        return (Variable(torch.zeros(batch_size, self.hidden_dim, height, width)).to(device),
                Variable(torch.zeros(batch_size, self.hidden_dim, height, width)).to(device))
# =========================================== This is in development ==================================================

class ConvBLSTM(nn.Module):
    def __init__(self, input_size, input_channels, hidden_channels, num_layers, device):
        super(ConvBLSTM, self).__init__()
        self.input_size = input_size
        self.input_channels = input_channels
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers
        self.device = device
        self.forward_net = ConvLSTM(input_size, input_channels, hidden_channels, kernel_size=(5, 5), num_layers=num_layers, device=device)
        self.reverse_net = ConvLSTM(input_size, input_channels, hidden_channels, kernel_size=(5, 5), num_layers=num_layers, device=device)
        self.conv_net = nn.Sequential(nn.Conv2d(2 * self.hidden_channels, 128, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(128, 256, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(256, 64, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(64, 1, kernel_size=5, padding=2))

    def forward(self, xforward, xreverse, hidden_fwd=None, prop_hidden=False):
        """
        xforward, xreverse = B T C H W tensors.
        """

        hidden_bwd = None
        '''if(hidden_fwd == None):
            hidden_bwd = None
        else:
            hidden_bwd = []
            for j in range(self.num_layers):
                hidden_bwd.append([torch.zeros_like(hidden_fwd[0][0]), torch.zeros_like(hidden_fwd[0][0])])'''

        y_out_fwd, hidden_fwd = self.forward_net(xforward, hidden_fwd, prop_hidden)
        y_out_rev, _ = self.reverse_net(xreverse, hidden_bwd, prop_hidden)

        # Take only the output of the last layer
        y_out_fwd = y_out_fwd[-1]
        y_out_rev = y_out_rev[-1]

        reversed_idx = list(reversed(range(y_out_rev.shape[1])))
        # reverse temporal outputs.
        y_out_rev = y_out_rev[:, reversed_idx, ...]
        ycat = torch.cat((y_out_fwd, y_out_rev), dim=2)

        curr_out = []
        for j in range(ycat.shape[1]):
            curr_out.append(self.conv_net(ycat[:, j]))
        out = torch.stack(curr_out, dim=1)

        if(prop_hidden):
            return out, hidden_fwd
        else:
            return out, None

class ConvLSTMCell(nn.Module):   # this is used

    def __init__(self, input_size, input_dim, hidden_dim, kernel_size, bias, device):
        """
        Initialize ConvLSTM cell.
        Parameters
        ----------
        input_size: (int, int)
            Height and width of input tensor as (height, width).
        input_dim: int
            Number of channels of input tensor.
        hidden_dim: int
            Number of channels of hidden state.
        kernel_size: (int, int)
            Size of the convolutional kernel.
        bias: bool
            Whether or not to add the bias.
        device: string
            Specify the device
        """

        super(ConvLSTMCell, self).__init__()

        self.height, self.width = input_size
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim

        self.kernel_size = kernel_size
        self.padding = kernel_size[0] // 2, kernel_size[1] // 2
        self.bias = bias
        self.device = device
        #  output channel = 4*hidden channel, because 4 conv2d is needed for the i,o,f,in gates.
        #amount of kernels needed : per in channel* per out* channel
        self.conv = nn.Conv2d(in_channels=self.input_dim + self.hidden_dim,
                              out_channels=4 * self.hidden_dim,
                              kernel_size=self.kernel_size,
                              padding=self.padding,
                              bias=self.bias)
        print('LASTconvlstmCELL123')

    def forward(self, input_tensor, cur_state):

        h_cur, c_cur = cur_state

        # concatenate along channel axis
        combined = torch.cat([input_tensor, h_cur], dim=1) # channel N = 2 for hidden Ch=1

        combined_conv = self.conv(combined)   
        cc_i, cc_f, cc_o, cc_g = torch.split(combined_conv, self.hidden_dim, dim=1)
        i = torch.sigmoid(cc_i)
        f = torch.sigmoid(cc_f)
        o = torch.sigmoid(cc_o)
        g = torch.tanh(cc_g)

        c_next = f * c_cur + i * g
        h_next = o * torch.tanh(c_next)

        return h_next, c_next

    def init_hidden(self, batch_size, tensor_size, device):
        height, width = tensor_size
        return (Variable(torch.zeros(batch_size, self.hidden_dim, height, width)).to(device),
                Variable(torch.zeros(batch_size, self.hidden_dim, height, width)).to(device))

class ConvLSTM(nn.Module):   # this is used
    def __init__(self, input_size, input_dim, hidden_dim, kernel_size, num_layers, device='cpu',
                 batch_first=False, bias=True, return_all_layers=False):
        super(ConvLSTM, self).__init__()

        self._check_kernel_size_consistency(kernel_size)

        # Make sure that both `kernel_size` and `hidden_dim` are lists having len == num_layers
        kernel_size = self._extend_for_multilayer(kernel_size, num_layers)
        hidden_dim = self._extend_for_multilayer(hidden_dim, num_layers)
        if not len(kernel_size) == len(hidden_dim) == num_layers:
            raise ValueError('Inconsistent list length.')

        self.height, self.width = input_size

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.kernel_size = kernel_size
        self.num_layers = num_layers
        self.device = device
        self.batch_first = batch_first
        self.bias = bias
        self.return_all_layers = return_all_layers
        
        print('LASTCONVLSTM2')
        cell_list = []
        for i in range(0, self.num_layers):
            cur_input_dim = self.input_dim if i == 0 else self.hidden_dim[i-1] #why? 

            cell_list.append(ConvLSTMCell(input_size=(self.height, self.width),
                                          input_dim=cur_input_dim,
                                          hidden_dim=self.hidden_dim[i],
                                          kernel_size=self.kernel_size[i],
                                          bias=self.bias, device=self.device))

        self.cell_list = nn.ModuleList(cell_list)

    def forward(self, input_tensor, hidden_state=None, prop_hidden=False):
        """
        Parameters
        ----------
        input_tensor:
            5-D Tensor either of shape (t, b, c, h, w) or (b, t, c, h, w)
        hidden_state:
            None.
        Returns
        -------
        last_state_list, layer_output
        """
        self.input_device = input_tensor.device
        # Implement stateful ConvLSTM
        if prop_hidden:
            self.return_all_layers = True
            if hidden_state == None:
                tensor_size = (input_tensor.size(3), input_tensor.size(4))
                hidden_state = self._init_hidden(batch_size=input_tensor.size(0), tensor_size=tensor_size)
        else:
            tensor_size = (input_tensor.size(3), input_tensor.size(4))
            hidden_state = self._init_hidden(batch_size=input_tensor.size(0), tensor_size=tensor_size)

        layer_output_list = []
        last_state_list = []

        seq_len = input_tensor.size(1)
        cur_layer_input = input_tensor

        for layer_idx in range(self.num_layers):

            h, c = hidden_state[layer_idx]
            output_inner = []
            for t in range(seq_len):

                h, c = self.cell_list[layer_idx](input_tensor=cur_layer_input[:, t, :, :, :],
                                                 cur_state=[h, c])
                output_inner.append(h)

            layer_output = torch.stack(output_inner, dim=1)
            cur_layer_input = layer_output

            layer_output_list.append(layer_output)
            last_state_list.append([h, c])

        if not self.return_all_layers:
            layer_output_list = layer_output_list[-1:]
            last_state_list = last_state_list[-1:]

        return layer_output_list, last_state_list

    def _init_hidden(self, batch_size, tensor_size):
        init_states = []
        for i in range(self.num_layers):
            init_states.append(self.cell_list[i].init_hidden(batch_size, tensor_size, self.input_device))
        return init_states

    @staticmethod
    def _check_kernel_size_consistency(kernel_size):
        if not (isinstance(kernel_size, tuple) or
                (isinstance(kernel_size, list) and all([isinstance(elem, tuple) for elem in kernel_size]))):
            raise ValueError('`kernel_size` must be tuple or list of tuples')

    @staticmethod
    def _extend_for_multilayer(param, num_layers):
        if not isinstance(param, list):  # if not param is not datatype= list then...
            param = [param] * num_layers
        return param

class ConvOverlapBLSTM_1Hidden(nn.Module):
    def __init__(self, input_size, input_channels, hidden_channels, num_layers, device):
        super(ConvOverlapBLSTM_1Hidden, self).__init__()
        ''' In development'''
        self.input_size = input_size
        self.input_channels = input_channels
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers
        self.device = device
        self.forward_net = ConvLSTM(input_size, input_channels, hidden_channels, kernel_size=(5, 5), num_layers=num_layers, device=device)
        self.reverse_net = ConvLSTM(input_size, input_channels, hidden_channels, kernel_size=(5, 5), num_layers=num_layers, device=device)
        self.conv_net = nn.Sequential(nn.Conv2d(2 * self.hidden_channels, 128, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(128, 256, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(256, 64, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(64, 1, kernel_size=5, padding=2))

    def forward(self, xforward, xreverse):
        """
        xforward, xreverse = B T C H W tensors.
        """

        y_out_fwd, hidden_fwd = self.forward_net(xforward)
        y_out_rev, _ = self.reverse_net(xreverse)

        # Take only the output of the last layer
        y_out_fwd = y_out_fwd[-1]
        y_out_rev = y_out_rev[-1]

        reversed_idx = list(reversed(range(y_out_rev.shape[1])))
        # reverse temporal outputs.
        y_out_rev = y_out_rev[:, reversed_idx, ...]
        ycat = torch.cat((y_out_fwd, y_out_rev), dim=2)

        curr_out = []
        for j in range(ycat.shape[1]):
            curr_out.append(self.conv_net(ycat[:, j]))
        out = torch.stack(curr_out, dim=1)

        return out

# =====================================================================================================================

class ConvOneDirectionalLSTM(nn.Module):
    def __init__(self, input_size, input_channels, hidden_channels, num_layers, device):
        super(ConvOneDirectionalLSTM, self).__init__()
        ''' In development'''
        self.input_size = input_size
        self.input_channels = input_channels
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers
        self.device = device
        self.forward_net = ConvLSTM(input_size, input_channels, hidden_channels,
                                    kernel_size=(5, 5), num_layers=num_layers, return_all_layers=True, device=device)
        self.conv_net = nn.Sequential(nn.Conv2d(self.hidden_channels, 128, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(128, 256, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(256, 64, kernel_size=5, padding=2),
                                      nn.ReLU(),
                                      nn.Conv2d(64, 1, kernel_size=5, padding=2))

    def forward(self, xforward, hidden_fwd):
        """
        xforward, xreverse = B T C H W tensors.
        """

        y_out_fwd, hidden_fwd = self.forward_net(xforward, hidden_fwd)

        # Take only the output of the last layer
        y_out_fwd = y_out_fwd[-1]

        curr_out = []
        for j in range(y_out_fwd.shape[1]):
            curr_out.append(self.conv_net(y_out_fwd[:, j]))
        out = torch.stack(curr_out, dim=1)

        return out, hidden_fwd
