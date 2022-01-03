"""
ANML Training Script
"""

import logging

import yaml

import utils.argparsing as argutils
from anml import train


if __name__ == "__main__":
    # Training settings
    parser = argutils.create_parser("ANML training")

    parser.add_argument("-c", "--config", metavar="PATH", type=argutils.existing_path, required=True,
                        help="Training config file.")
    argutils.add_dataset_arg(parser, add_train_size_arg=True)
    parser.add_argument("--rln", metavar="NUM_CHANNELS", type=int, default=256,
                        help="Number of channels to use in the RLN.")
    parser.add_argument("--nm", metavar="NUM_CHANNELS", type=int, default=112,
                        help="Number of channels to use in the NM.")
    parser.add_argument("--batch-size", metavar="INT", type=int, default=1,
                        help="Number of examples per training batch in the inner loop.")
    parser.add_argument("--num-batches", metavar="INT", type=int, default=20,
                        help="Number of training batches in the inner loop.")
    parser.add_argument("--train-cycles", metavar="INT", type=int, default=1,
                        help="Number of times to run through all training batches, to comprise a single outer loop."
                        " Total number of gradient updates will be num_batches * train_cycles.")
    parser.add_argument("--val-size", metavar="INT", type=int, default=200,
                        help="Total number of test examples to sample from the validation set each iteration (for"
                             " testing generalization to never-seen examples).")
    parser.add_argument("--remember-size", metavar="INT", type=int, default=64,
                        help="Number of randomly sampled training examples to compute the meta-loss.")
    parser.add_argument("--remember-only", action="store_true",
                        help="Do not include the training examples from the inner loop into the meta-loss (only use"
                             " the remember set for the outer loop of training).")
    parser.add_argument("--inner-lr", metavar="RATE", type=float, default=1e-1, help="Inner learning rate.")
    parser.add_argument("--outer-lr", metavar="RATE", type=float, default=1e-3, help="Outer learning rate.")
    parser.add_argument("--save-freq", type=int, default=1000, help="Number of epochs between each saved model.")
    parser.add_argument("--epochs", type=int, default=30000, help="Number of epochs to train.")
    argutils.add_device_arg(parser)
    argutils.add_seed_arg(parser, default_seed=1)
    argutils.add_verbose_arg(parser)

    args = parser.parse_args()
    argutils.configure_logging(args, level=logging.INFO)

    with open(args.config, 'r') as f:
        config = yaml.full_load(f)

    # Command line args optionally override config.
    overrideable_args = ["dataset", "data_path", "download", "im_size", "train_size", "batch_size", "num_batches",
                         "train_cycles", "val_size", "remember_size", "remember_only", "inner_lr", "outer_lr",
                         "save_freq", "epochs", "seed"]
    for arg in overrideable_args:
        # Only replace if value is different from default (meaning it was explicitly specified by the user), or if the
        # value doesn't already exist in config.
        dflt = parser.get_default(arg)
        value = getattr(args, arg, dflt)
        if arg not in config or value != dflt:
            config[arg] = value

    device = argutils.get_device(parser, args)
    argutils.set_seed(config["seed"])
    sampler, input_shape = argutils.get_OML_dataset_sampler(config)

    logging.info("Commencing training.")
    train(sampler, input_shape, config, device, args.verbose)
    logging.info("Training complete.")
