import logging
from pathlib import Path

import higher
import numpy as np
import torch
from torch.nn.functional import cross_entropy
from torch.nn.init import kaiming_normal_
from torch.optim import SGD, Adam

import utils.storage as storage
from models import ANML, LegacyANML, recommended_number_of_convblocks
from utils import divide_chunks
from utils.logging import Log


def create_model(input_shape, nm_channels, rln_channels, device):
    # TODO: Auto-size this instead.
    # num_classes = max(sampler.num_train_classes(), sampler.num_test_classes())
    num_classes = 1000
    # For backward compatibility, we use the original ANML if the images are <=30 px.
    # Otherwise, we automatically size the net as appropriate.
    if input_shape[-1] <= 30:
        # Temporarily turn off the "legacy" model so we can test parity with the new model.
        # model_args = {
        #     "input_shape": input_shape,
        #     "rln_chs": rln_channels,
        #     "nm_chs": nm_channels,
        #     "num_classes": num_classes,
        # }
        # anml = LegacyANML(**model_args)
        model_args = {
            "input_shape": input_shape,
            "rln_chs": rln_channels,
            "nm_chs": nm_channels,
            "num_classes": num_classes,
            "num_conv_blocks": 3,
            "pool_rln_output": False,
        }
        anml = ANML(**model_args)
    else:
        model_args = {
            "input_shape": input_shape,
            "rln_chs": rln_channels,
            "nm_chs": nm_channels,
            "num_classes": num_classes,
            "num_conv_blocks": recommended_number_of_convblocks(input_shape),
            "pool_rln_output": True,
        }
        anml = ANML(**model_args)
    anml.to(device)
    logging.info(f"Model shape:\n{anml}")
    return anml, model_args


def load_model(model_path, sampler_input_shape):
    model_path = Path(model_path).resolve()
    if model_path.suffix == ".net":
        # Assume this was saved by the storage module, which pickles the entire model.
        model = storage.load(model_path)
    elif model_path.suffix == ".pt" or model_path.suffix == ".pth":
        # Assume the model was saved in the legacy format:
        #   - Only state_dict is stored.
        #   - Model shape is identified by the filename.
        sizes = [int(num) for num in model_path.name.split("_")[:-1]]
        if len(sizes) != 3:
            raise RuntimeError(f"Unsupported model shape: {sizes}")
        rln_chs, nm_chs, mask_size = sizes
        if mask_size != (rln_chs * 9):
            raise RuntimeError(f"Unsupported model shape: {sizes}")

        # Backward compatibility: Before we constructed the network based on `input_shape` and `num_classes`. At this
        # time, `num_classes` was always 1000 and we always used greyscale 28x28 images.
        input_shape = (1, 28, 28)
        out_classes = 1000
        model = LegacyANML(input_shape, rln_chs, nm_chs, out_classes)
        model.load_state_dict(torch.load(model_path, map_location="cpu"))
    else:
        supported = (".net", ".pt", ".pth")
        raise RuntimeError(f"Unsupported model file type: {model_path}. Expected one of {supported}.")

    logging.debug(f"Model shape:\n{model}")

    # Check if the images we are testing on match the dimensions of the images this model was built for.
    if tuple(model.input_shape) != tuple(sampler_input_shape):
        raise RuntimeError("The specified dataset image sizes do not match the size this model was trained for.\n"
                           f"Data size:  {sampler_input_shape}\n"
                           f"Model size: {model.input_shape}")
    return model


def lobotomize(layer, class_num):
    kaiming_normal_(layer.weight[class_num].unsqueeze(0))


def train(
        sampler,
        input_shape,
        rln_channels,
        nm_channels,
        train_size=20,
        remember_size=64,
        inner_lr=1e-1,
        outer_lr=1e-3,
        its=30000,
        device="cuda",
        verbose=False
):
    assert inner_lr > 0
    assert outer_lr > 0
    assert its > 0

    anml, model_args = create_model(input_shape, nm_channels, rln_channels, device)

    # Set up progress/checkpoint logger. Name according to the supported input size, just for convenience.
    name = "ANML-" + "-".join(map(str, input_shape))
    print_freq = 1 if verbose else 10
    log = Log(name, model_args, print_freq)

    # inner optimizer used during the learning phase
    inner_opt = SGD(list(anml.rln.parameters()) + list(anml.fc.parameters()), lr=inner_lr)
    # outer optimizer used during the remembering phase; the learning is propagated through the inner loop
    # optimizations, computing second order gradients.
    outer_opt = Adam(anml.parameters(), lr=outer_lr)

    for it in range(its):

        train_data, train_class, (valid_ims, valid_labels) = sampler.sample_train(
            train_size=train_size,
            remember_size=remember_size,
            device=device,
        )

        # To facilitate the propagation of gradients through the model we prevent memorization of
        # training examples by randomizing the weights in the last fully connected layer corresponding
        # to the task that is about to be learned
        lobotomize(anml.fc, train_class)

        # higher turns a standard pytorch model into a functional version that can be used to
        # preserve the computation graph across multiple optimization steps
        with higher.innerloop_ctx(anml, inner_opt, copy_initial_weights=False) as (
                fnet,
                diffopt,
        ):
            # Inner loop of 1 random task (20 images), one by one
            for im, label in train_data:
                out = fnet(im)
                loss = cross_entropy(out, label)
                diffopt.step(loss)

            # Outer "loop" of 1 task (20 images) + 64 random chars, one batch of 84,1,28,28
            m_out = fnet(valid_ims)
            m_loss = cross_entropy(m_out, valid_labels)
            correct = (m_out.argmax(axis=1) == valid_labels).sum().item()
            m_acc = correct / len(valid_labels)
            m_loss.backward()

        outer_opt.step()
        outer_opt.zero_grad()

        log(it, m_loss, m_acc, anml)

    log.close(it, anml)


def test_test(model, test_data, test_examples=5):
    # Meta-test-test
    # given a meta-test-trained model, evaluate accuracy on the held out set
    # of classes used
    x, y = test_data
    with torch.no_grad():
        logits = model(x)
        # report performance per class
        ys = list(divide_chunks(y, test_examples))
        tasks = list(divide_chunks(logits, test_examples))
        t_accs = [
            torch.eq(task.argmax(dim=1), ys).sum().item() / test_examples
            for task, ys in zip(tasks, ys)
        ]
    return t_accs


def test_train(
        model_path,
        sampler,
        sampler_input_shape,
        num_classes=10,
        train_examples=15,
        device="cuda",
        lr=0.01,
):
    model = load_model(model_path, sampler_input_shape)
    model = model.to(device)

    torch.nn.init.kaiming_normal_(model.fc.weight)
    model.nm.requires_grad_(False)
    model.rln.requires_grad_(False)

    test_examples = 20 - train_examples
    train_tasks, test_data = sampler.sample_test(num_classes, train_examples, test_examples, device)

    opt = torch.optim.Adam(model.parameters(), lr=lr)

    for task in train_tasks:
        # meta-test-TRAIN
        for x, y in task:
            logits = model(x)
            opt.zero_grad()
            loss = cross_entropy(logits, y)
            loss.backward()
            opt.step()

    # meta-test-TEST
    t_accs = np.array(test_test(model, test_data, test_examples))

    return t_accs
