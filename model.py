import torch
import torch.nn as nn
import torch.nn.functional as F


class ConvBlock(nn.Module):
    def __init__(self, in_channels, out_channels, pooling=True):
        super(ConvBlock, self).__init__()
        self.pooling = pooling
        self.conv = nn.Conv2d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=(3, 3),
            stride=1,
            padding=0,
        )
        self.norm = nn.InstanceNorm2d(out_channels, affine=True)

    def forward(self, x):
        x = self.conv(x)
        x = self.norm(x)
        x = torch.relu(x)
        if self.pooling:
            x = F.max_pool2d(x, kernel_size=2, stride=2)
        return x


class RLN(nn.Module):
    def __init__(self, in_channels, hidden_channels):
        super(RLN, self).__init__()
        self.convBlock1 = ConvBlock(in_channels, hidden_channels)
        self.convBlock2 = ConvBlock(hidden_channels, hidden_channels)
        self.convBlock3 = ConvBlock(hidden_channels, hidden_channels, pooling=False)

    def forward(self, x):
        x = self.convBlock1(x)
        x = self.convBlock2(x)
        x = self.convBlock3(x)

        x = torch.flatten(x, start_dim=1)
        return x


class NM(nn.Module):
    def __init__(self, input_shape, hidden_channels, mask_size):
        super(NM, self).__init__()
        self.convBlock1 = ConvBlock(input_shape[0], hidden_channels)
        self.convBlock2 = ConvBlock(hidden_channels, hidden_channels)
        self.convBlock3 = ConvBlock(hidden_channels, hidden_channels, pooling=False)
        # To create the correct size of linear layer, we need to first know the size of the conv output.
        batch_shape = (2,) + tuple(input_shape)
        shape_after_conv = self.forward_conv(torch.zeros(batch_shape)).shape
        assert len(shape_after_conv) == 2, "Conv output should only be two dims."
        self.fc = nn.Linear(in_features=shape_after_conv[-1], out_features=mask_size)

    def forward_conv(self, x):
        x = self.convBlock1(x)
        x = self.convBlock2(x)
        x = self.convBlock3(x)
        x = torch.flatten(x, start_dim=1)
        return x

    def forward_linear(self, x):
        x = self.fc(x)
        x = torch.sigmoid(x)
        return x

    def forward(self, x):
        x = self.forward_conv(x)
        x = self.forward_linear(x)
        return x


class ANML(nn.Module):
    def __init__(self, input_shape, rln_chs, nm_chs, num_classes=1000):
        super(ANML, self).__init__()
        self.rln = RLN(input_shape[0], rln_chs)
        # Automatically determine what the size of the final layer needs to be.
        # Simulate a batch by adding an extra dim at the beginning.
        batch_shape = (2,) + tuple(input_shape)
        shape_after_rln = self.rln(torch.zeros(batch_shape)).shape
        assert len(shape_after_rln) == 2, "RLN output should only be two dims."
        feature_size = shape_after_rln[-1]
        self.nm = NM(input_shape, nm_chs, feature_size)
        self.fc = nn.Linear(feature_size, num_classes)

    def forward(self, x):
        features = self.rln(x)
        nm_mask = self.nm(x)

        features = features * nm_mask

        out = self.fc(features)

        return out
