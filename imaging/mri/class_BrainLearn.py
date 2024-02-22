import os
import csv
import glob
import random

import monai.config
import numpy as np
import torch
import nibabel as nib
import tqdm
import datetime
import SimpleITK as sitk
from utilities import closest_divisible_by_power_of_two
from monai.networks.nets import UNet, UNETR
from monai.transforms import Compose, LoadImaged, EnsureChannelFirstd, Spacingd, OrientationD, ScaleIntensityRanged, \
    AsDiscreted, AsDiscrete, SpacingD, SpatialCropD, MapTransform, Transform, LambdaD, RandSpatialCropD, ToTensorD
from monai.data import CacheDataset, DataLoader, decollate_batch
from monai.inferers import sliding_window_inference

from monai.losses import DiceLoss
from monai.losses import MultiScaleLoss
from monai.metrics import DiceMetric
from monai.losses.ssim_loss import SSIMLoss

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from skimage.color import label2rgb
from skimage.measure import label, regionprops
from skimage.exposure import rescale_intensity
from skimage.morphology import ball, remove_small_objects, binary_opening, binary_closing


class BrainLearn:
    """This class is a wrapper around MONAI. It handles dataset preparation, model training, validating
    and testing. It includes libraries for plotting (in dev.), and statistical analysis (in dev.). """
    def __init__(self):
        # The class assumes by default data are stored in main folder -> [data, dataset, model,...]
        # however these subdirectory can be changed by changing the corresponding protected attributes.
        # Note: main -> dataset includes training, validation and testing datasets
        self.path_main = None
        self._path_dataset = "dataset"
        self._path_data_raw = "raw"
        self._path_model = "model"
        self._path_results = "results"
        self.experiment = None  # experiment name. Generated by the generate_experiment_name method launched
        # at the beginning of the training phase.
        # MODEL
        self.model = None
        self.n_classes = 2
        self.optimizer = None
        self.max_iteration_trn = 100  # max iteration for training
        self.delta_iteration_trn = 1  # number of iterations in-between each validation step
        self.roi_size = None
        # TRANSFORMATIONS
        self.intensity_min = -1.0  # min voxel intensity value, used for intensity rescaling
        self.intensity_max = 1.0  # max voxel intensity value, used for intensity rescaling
        self.transforms_trn = None
        self.transforms_val = None
        self.transforms_tst = None
        # IMAGE METADATA
        self.voxel = (1.0, 1.0, 1.0)  # voxel dimensions (dx, dy, dz) in mm
        # DATA AND LOADERS
        self.data_percentage = 1
        self.dataset_trn_ratio = 0.7  # percentage of data used for training
        self.dataset_val_ratio = 0.2  # percentage of data used for validating
        self.dataset_tst_ratio = 0.1  # percentage of data used for testing
        self.batch_trn_size = 1  # training dataset batch size
        self.batch_val_size = 1  # validation dataset batch size
        self.batch_tst_size = 1  # testing dataset batch size
        self.dataset_trn: list or None = None  # list of dictionary for training the model (compiled by method)
        self.dataset_val: list or None = None  # list of dictionary for validating the model (compiled by method)
        self.dataset_tst: list or None = None  # list of dictionary for testing the model (compiled by method)
        self.loader_trn = None  # training data loader (compiled by method)
        self.loader_val = None  # validation data loader (compiled by method)
        self.loader_tst = None  # testing data loader (compiled by method)
        # MODEL PERFORMANCE INDEXES
        self.loss_function = None
        self.metric_function = None
        self.epochs = None
        self.losses = None
        self.scores = None
        # HARDWARE
        self.device = torch.device(self.set_gpu())

        monai.config.print_config()

    def generate_experiment_name(self):
        """the experiment name is the datetime. Model info are saved in a text file"""
        self.experiment = datetime.datetime.now().strftime('%Y.%m.%d %H.%M.%S')

    def set_optimizer(self, optimizer="adam"):
        """Set the model optimizer."""
        if self.model is None:
            return print("Please build a model before setting the optimizer...")
        else:
            if optimizer == "adam":
                self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=1E-3, weight_decay=1E-4)
            if optimizer == "sgd":
                self.optimizer = torch.optim.SGD(self.model.parameters(), lr=1E-2, weight_decay=0)

    def set_loss_function(self, loss_function="l1"):
        """Set the loss function for training"""
        if loss_function == "l1":
            self.loss_function = torch.nn.L1Loss()
        if loss_function == "l2":
            self.loss_function = torch.nn.MSELoss()
        if loss_function == "ssim":
            self.loss_function = SSIMLoss(spatial_dims=3, )
        if loss_function == "mssim":
            self.loss_function = MultiScaleLoss(loss=SSIMLoss(),
                                                scales=[1, 2, 4],
                                                kernel="gaussian",
                                                reduction=None)

    def set_metric_function(self, metric_function="l1"):
        """Set the metric function for validation and testing"""
        if metric_function == "l1":
            self.metric_function = torch.nn.L1Loss()
        if metric_function == "l2":
            self.metric_function = torch.nn.MSELoss()
        if metric_function == "ssim":
            self.metric_function = None
        if metric_function == "mssim":
            self.metric_function = None

    def set_gpu(self):
        """check gpu availability and set device to use"""
        if torch.cuda.is_available():
            var = "cuda"
        elif torch.backends.mps.is_available() and torch.backends.mps.is_built():
            var = "mps"
        else:
            var = "cpu"
        return var

    def get_voxel_size(self):
        """extract voxel dimensions from first image in training dataset"""
        if not self.dataset_trn:
            print("Error: dataset_trn is empty. Build datasets first.")
            return
        self.voxel[0], self.voxel[1], self.voxel[2] = nib.load(self.dataset_trn[0]["img"]).header.get_zooms()

    def save_model(self):
        torch.save(self.model, os.path.join(self.path_model, self.experiment))

    def load_model(self, model):
        self.model = torch.load(os.path.join(self.path_main, self.path_model, model), map_location=self.device)

    def save_model_attributes_to_csv(self, filename):
        """save model attributes to CSV"""
        data_to_save = {f"{key}": val for (key, val) in vars(self.model)}
        with open(filename, "w", newline="") as file:
            writer = csv.writer(file)
            # Write the header
            writer.writerow(["Attribute", "Value"])
            # Write the attribute-value pairs
            for attr, value in data_to_save.items():
                writer.writerow([attr, value])

    def load_model_attributes_from_csv(self, filename):
        """load model attributes from CSV"""
        data = {}
        with open(filename, "r") as file:
            reader = csv.reader(file)
            # Skip the header
            next(reader, None)
            # Read attribute-value pairs
            for row in reader:
                attr, value = row
                data[attr] = eval(value)  # Use eval to convert the string back to its original type
        for key, val in data.items():
            self.model.setattr(self, key, val)

    def compose_transforms_trn(self):
        """Compose the transformation for the training dataset"""
        self.transforms_trn = Compose([
            #LoadImaged(keys=["img1", "img2"]),
            self.LoadImageAndTextD(image_keys=["img1", "img2"], text_keys=["roi"]),
            EnsureChannelFirstd(keys=["img1", "img2"]),
            RandSpatialCropD(keys=["img1", "img2"], roi_size=(128, 128, 128)),
            #self.CropImageBasedOnROI(img_keys=["img1", "img2"], roi_key="roi", roi_size=self.roi_size),
            ToTensorD(keys=["img1", "img2"]),
            #LambdaD(keys=["img1", "img2"],)
            #self.CropImageBasedOnROI(keys=["img1", "img2"], roi_size=self.roi_size),
            # SpatialCropD(keys=["img"], roi_size=, roi_start=, roi_end=),
            # AsDiscreted(
            #     keys=["lbl"], to_onehot=self.n_classes),
            # Spacingd(
            #     keys=["img", "msk"],
            #     pixdim=(self.voxel[0], self.voxel[1], self.voxel[2]),
            #     mode=("bilinear", "nearest")),
            # OrientationD(
            #     keys=["img", "lbl"],
            #     axcodes="RAS"),
            # ScaleIntensityRanged(
            #     keys=["img"],
            #     a_min=self.intensity_min,
            #     a_max=self.intensity_max,
            #     b_min=0.0,
            #     b_max=1.0,
            #     clip=True),
        ])

    def compose_transforms_val(self):
        """compose the transformation for the validation dataset"""
        self.transforms_val = Compose([
            LoadImaged(keys=["img", "lbl"]),
            EnsureChannelFirstd(keys=["img", "lbl"]),
            #AsDiscreted(keys=["lbl"], to_onehot=self.n_classes),
            #Spacingd(keys=["img", "lbl"], pixdim=(self.voxel[0], self.voxel[1], self.voxel[2]),
            #         mode=("bilinear", "nearest")),
            #OrientationD(keys=["img", "lbl"], axcodes="RAS"),
            #ScaleIntensityRanged(keys=["img"], a_min=self.intensity_min, a_max=self.intensity_max,
            #                     b_min=0.0, b_max=1.0, clip=True),
        ])

    def compose_transforms_tst(self):
        """compose the transformation for the testing dataset"""
        self.transforms_tst = Compose([
            LoadImaged(keys=["img", "lbl"]),
            EnsureChannelFirstd(keys=["img", "lbl"]),
            AsDiscreted(keys=["lbl"], to_onehot=self.n_classes),
            Spacingd(keys=["img", "lbl"], pixdim=(self.voxel[0], self.voxel[1], self.voxel[2]),
                     mode=("bilinear", "nearest")),
            OrientationD(keys=["img", "lbl"], axcodes="RAS"),
            ScaleIntensityRanged(keys=["img"], a_min=self.intensity_min, a_max=self.intensity_max,
                                 b_min=0.0, b_max=1.0, clip=True),
        ])

    def build_dataset(self):
        """
        Build training, validation and testing datasets.
        Each sample is a dictionary {img T1wC0.5, img T1wC1.0, msk, roi}, where 'img T1wC0.5' is the path to the
        image with C0.5 contrast dose, 'img T1wC1.0' is the path to the image with C1.0 contrast dose,
        'msk' is the path to the brain mask, and 'roi' is the ROI used to crop the image for memory-efficient training.
        """

        # Create dictionary
        path_im1 = sorted(glob.glob(os.path.join(self.path_main, "dataset", "*T1wRC0.5.nii")))
        path_im2 = sorted(glob.glob(os.path.join(self.path_main, "dataset", "*T1wRC1.0.nii")))
        #path_msk = sorted(glob.glob(os.path.join(self.path_main, "dataset", "*T1wRC0.0.msk.nii")))
        path_roi = sorted(glob.glob(os.path.join(self.path_main, "dataset", "*T1wRC0.0.info.txt")))
        #path_dic = [{"img1": img1, "img2": img2, "msk": msk, "roi": roi} for img1, img2, msk, roi in zip(path_im1, path_im2, path_msk, path_roi)]
        path_dic = [{"img1": img1, "img2": img2, "roi": roi} for img1, img2, roi in zip(path_im1, path_im2, path_roi)]

        # select subset of data
        if self.data_percentage < 1:
            path_dic = path_dic[:int(len(path_dic) * self.data_percentage)]

        # Calculate the number of samples for each split
        n = len(path_dic)
        n_tra = int(self.dataset_trn_ratio * n)
        n_val = int(self.dataset_val_ratio * n)

        # Shuffle the data list to randomize the order
        random.shuffle(path_dic)

        # Split the data into training, validation, and testing sets, and store paths in attributes
        self.dataset_trn = path_dic[:n_tra]
        self.dataset_val = path_dic[n_tra:n_tra + n_val]
        self.dataset_tst = path_dic[n_tra + n_val:]

    def cache_dataset_trn(self):
        """cache training dataset and generate loader"""
        if self.dataset_trn:
            dataset_trn = CacheDataset(data=self.dataset_trn, transform=self.transforms_trn)
            self.loader_trn = DataLoader(dataset_trn, batch_size=self.batch_trn_size)

    def cache_dataset_val(self):
        """cache validation dataset and generate loader"""
        if self.dataset_val:
            dataset_val = CacheDataset(data=self.dataset_val, transform=self.transforms_val)
            self.loader_val = DataLoader(dataset_val, batch_size=self.batch_val_size)

    def cache_dataset_tst(self):
        """cache testing dataset and generate loader"""
        if self.dataset_tst:
            dataset_tst = CacheDataset(data=self.dataset_tst, transform=self.transforms_tst)
            self.loader_tst = DataLoader(dataset_tst, batch_size=self.batch_tst_size)

    def build_model_unet(self):  # weight decay of the Adam optimizer
        """Build a UNet model"""
        model = UNet(
            spatial_dims=3,
            in_channels=1,
            out_channels=self.n_classes,
            channels=(64, 128, 256, 512, 1024),  # sequence of channels. Top block first. len(channels) >= 2
            strides=(2, 2, 2, 2),  # sequence of convolution strides. len(strides) = len(channels) - 1.
            kernel_size=3, # convolution kernel size, value(s) should be odd. If sequence, length = N. layers.
            up_kernel_size=3, # de-convolution kernel size, value(s) should be odd. If sequence, length = N. layers
            num_res_units=1,  # number of residual units. Defaults to 0.
            # act=params["activation_function"],
            dropout=0
        ).to(self.device)
        self.model = model

    def train(self):
        """Train the model"""
        # generate the experiment name
        self.generate_experiment_name()
        # generate arrays for training and validating plotting purposes
        self.epochs = np.arange(self.max_iteration_trn)
        self.losses = np.zeros(self.max_iteration_trn)
        self.scores = np.zeros(self.max_iteration_trn)
        for epoch in range(self.max_iteration_trn):
            # set the model to training. This has effect only on some transforms
            self.model.train()
            # initialize the epoch's loss
            epoch_loss = 0
            epoch_trn_iterator = tqdm.tqdm(self.loader_trn, desc="Training (X / X Steps) (loss=X.X)", dynamic_ncols=True, miniters=1)
            for step_trn, batch_trn in enumerate(epoch_trn_iterator):
                # reset the optimizer (which stores the values from the previous iteration
                self.optimizer.zero_grad()
                # send the training data to device (GPU)
                inputs, targets = batch_trn['img'].to(self.device), batch_trn['lbl'].to(self.device)
                # forward pass
                outputs = self.model(inputs)
                # calculate loss and add it to epoch's loss
                loss = self.loss_function(outputs, targets)
                epoch_loss += loss.item()
                # backpropagation
                loss.backward()
                # update metrics
                self.optimizer.step()
                # Update the progress bar description with loss and metrics
                epoch_trn_iterator.set_description(f"Training ({epoch + 1} / {self.max_iteration_trn} Steps) (loss={epoch_loss:2.5f})")
            # store epoch's loss in losses array
            self.losses[epoch] = epoch_loss
            # validate model every "delta_iteration"
            if epoch == 0 or (epoch + 1) % self.delta_iteration_trn == 0 or (epoch + 1) == self.max_iteration_trn:
                # run validation
                score = self.validate(epoch)
                # store validation metrics in metrics array
                self.scores[epoch] = score
                # save model to disc
                torch.save(self.model, os.path.join(self.path_model, f"{self.experiment} - iter {epoch + 1:03d} mdl.pth"))

    def validate(self, epoch):
        """Validate the model"""
        # set validation metric to zero
        epoch_score = 0
        # set the model to validation. This affects some transforms.
        self.model.eval()
        # disable gradient computation (which is useless for validation)
        with torch.no_grad():
            epoch_val_iterator = tqdm.tqdm(self.loader_val, desc="Validate (X / X Steps) (dice=X.X)", dynamic_ncols=True, miniters=1)
            for step_val, batch_val in enumerate(epoch_val_iterator):
                # reset dice metrics for next validation round.
                # ???? Do we do this in between batches ???
                self.metric_function.reset()
                # send the validation data to device (GPU)
                img_val, lbl_val = batch_val["img"].to(self.device), batch_val["lbl"].to(self.device)
                # run inference by forward passing the input data through the model
                prd_val = self.model(img_val)
                # binarize the prediction (as required by the dice metric)
                prd_val = AsDiscrete(threshold=0.5)(prd_val)
                self.metric_function(y_pred=prd_val, y=lbl_val)
                # evaluate metric. ??? Need to aggregate first ??? and then collect the item.
                batch_score = self.metric_function.aggregate().item()
                # add batch's validation metric and then calculate average metric
                epoch_score += batch_score
                score_mean = epoch_score / (step_val + 1)
                # Update the progress bar description with metric
                epoch_val_iterator.set_description(f"Validate ({epoch + 1} / {self.max_iteration_trn} Steps) (dice={score_mean:2.5f})")
        return score_mean

    def testing(self):
        """test model on a testing dataset"""

    # plot training loss vs epoch
    def plot_loss(self, show=False):
        plt.figure()
        plt.plot(self.epochs, self.losses, lw=0, ms=6, marker='o', color='black')
        plt.title("Training Loss")
        plt.xlabel("Epoch")
        plt.ylabel("Loss")
        plt.tight_layout()
        if show:
            plt.show()
        plt.savefig(os.path.join(self.path_model, f"{self.experiment} loss vs epoch.png"))

    # plot validation metric vs epoch
    def plot_metr(self, show=False):
        plt.figure()
        plt.plot(self.epochs[self.metrics != 0], self.metrics[self.metrics != 0], lw=0, ms=6, marker='o', color='black')
        plt.title("Validation Metric")
        plt.xlabel("Epoch")
        plt.ylabel("Metric")
        plt.tight_layout()
        if show:
            plt.show()
        plt.savefig(os.path.join(self.path_model, f"{self.experiment} metric vs epoch.png"))

    # plot segmentation
    def testing(self, morph):
        epoch_tst_iterator = tqdm.tqdm(self.loader_tst, desc="Validating (X / X Steps)", dynamic_ncols=True, miniters=1)
        for step_tst, batch_tst in enumerate(epoch_tst_iterator):
            epoch_tst_iterator.set_description(f"Validating ({step_tst + 1} / {len(epoch_tst_iterator)} Steps)")
            img_tst, lbl_tst = batch_tst["img"].to(self.device), batch_tst["lbl"].to(self.device)
            lbl_prd = self.model(img_tst)
            # convert tensors from one hot encoding to single channel
            img_tst = img_tst[0, 0, :, :, :].cpu().numpy()
            lbl_tst = torch.argmax(lbl_tst, dim=1)[0, :, :, :].cpu().numpy()
            lbl_prd = torch.argmax(lbl_prd, dim=1)[0, :, :, :].cpu().numpy()
            # morphological operation: erosion
            if morph is True:
                for n in range(2):
                    lbl_prd = binary_opening(lbl_prd, ball(2))
                lbl_prd = binary_closing(lbl_prd, ball(1))
                lbl_prd = remove_small_objects(lbl_prd, 64)
            print(f"\nPlotting sample {step_tst}...")
            # Find the (x,y,z) coordinates of the sample center
            x = int(np.floor(img_tst.shape[0] / 2))
            y = int(np.floor(img_tst.shape[1] / 2))
            z = int(np.floor(img_tst.shape[2] / 2))
            # run a connectivity analysis on test and prediction
            lbl_tst = label(lbl_tst, connectivity=1)
            rgn_tst = regionprops(lbl_tst)
            n_rgn_tst = len(rgn_tst)
            lbl_prd = label(lbl_prd, connectivity=1)
            rgn_prd = regionprops(lbl_prd)
            n_rgn_prd = len(rgn_prd)
            print(f"N. regions ground truth: {n_rgn_tst}\n"
                  f"N. regions prediction: {n_rgn_prd}\n"
                  f"Relative error (%): {(n_rgn_prd - n_rgn_tst) / n_rgn_tst * 100}")
            # Generate overlay images
            img_tst_x = img_tst[x, :, :]
            img_tst_y = img_tst[:, y, :]
            img_tst_z = img_tst[:, :, z]
            lbl_tst_x = lbl_tst[x, :, :]
            lbl_tst_y = lbl_tst[:, y, :]
            lbl_tst_z = lbl_tst[:, :, z]
            lbl_prd_x = lbl_prd[x, :, :]
            lbl_prd_y = lbl_prd[:, y, :]
            lbl_prd_z = lbl_prd[:, :, z]
            img_label_overlay_x_tst = label2rgb(label=lbl_tst_x, image=rescale_intensity(img_tst_x, out_range=(0, 1)),
                                                alpha=0.3, bg_label=0, bg_color=None, kind="overlay", saturation=0.6)
            img_label_overlay_y_tst = label2rgb(label=lbl_tst_y, image=rescale_intensity(img_tst_y, out_range=(0, 1)),
                                                alpha=0.3, bg_label=0, bg_color=None, kind="overlay", saturation=0.6)
            img_label_overlay_z_tst = label2rgb(label=lbl_tst_z, image=rescale_intensity(img_tst_z, out_range=(0, 1)),
                                                alpha=0.3, bg_label=0, bg_color=None, kind="overlay", saturation=0.6)
            img_label_overlay_x_prd = label2rgb(label=lbl_prd_x, image=rescale_intensity(img_tst_x, out_range=(0, 1)),
                                                alpha=0.3, bg_label=0, bg_color=None, kind="overlay", saturation=0.6)
            img_label_overlay_y_prd = label2rgb(label=lbl_prd_y, image=rescale_intensity(img_tst_y, out_range=(0, 1)),
                                                alpha=0.3, bg_label=0, bg_color=None, kind="overlay", saturation=0.6)
            img_label_overlay_z_prd = label2rgb(label=lbl_prd_z, image=rescale_intensity(img_tst_z, out_range=(0, 1)),
                                                alpha=0.3, bg_label=0, bg_color=None, kind="overlay", saturation=0.6)
            # plot
            fig, axs = plt.subplots(2, ncols=3)
            axs[0, 0].set_title(f"YZ plane at X = {x} px")
            axs[0, 0].imshow(img_label_overlay_x_tst)
            axs[1, 0].imshow(img_label_overlay_x_prd)
            axs[0, 1].set_title(f"XZ plane at Y = {y} px")
            axs[0, 1].imshow(img_label_overlay_y_tst)
            axs[1, 1].imshow(img_label_overlay_y_prd)
            axs[0, 2].set_title(f"XY plane at Z = {z} px")
            axs[0, 2].imshow(img_label_overlay_z_tst)
            axs[1, 2].imshow(img_label_overlay_z_prd)
            # Remove x-axis and y-axis ticks, labels, and tick marks for all subplots
            for ax in axs.flat:
                ax.set_xticks([])
                ax.set_yticks([])
                ax.set_xticklabels([])
                ax.set_yticklabels([])
            # Adjust layout for better spacing
            plt.tight_layout()
            # Save the figure to a file
            plt.savefig(os.path.join("test", f"{self.params['experiment']} img {step_tst:02d} xyz {x, y, z}.png"),
                        dpi=1200)
            # Add rectangles around regions
            regions = regionprops(lbl_tst_x)
            for region in regions:
                minr, minc, maxr, maxc = region.bbox
                rect = Rectangle((minc, minr), maxc - minc, maxr - minr, fill=False, edgecolor='red', linewidth=0.3)
                axs[0, 0].add_patch(rect)
            regions = regionprops(lbl_tst_y)
            for region in regions:
                minr, minc, maxr, maxc = region.bbox
                rect = Rectangle((minc, minr), maxc - minc, maxr - minr, fill=False, edgecolor='red', linewidth=0.3)
                axs[0, 1].add_patch(rect)
            regions = regionprops(lbl_tst_z)
            for region in regions:
                minr, minc, maxr, maxc = region.bbox
                rect = Rectangle((minc, minr), maxc - minc, maxr - minr, fill=False, edgecolor='red', linewidth=0.3)
                axs[0, 2].add_patch(rect)
            regions = regionprops(lbl_prd_x)
            for region in regions:
                minr, minc, maxr, maxc = region.bbox
                rect = Rectangle((minc, minr), maxc - minc, maxr - minr, fill=False, edgecolor='red', linewidth=0.3)
                axs[1, 0].add_patch(rect)
            regions = regionprops(lbl_prd_y)
            for region in regions:
                minr, minc, maxr, maxc = region.bbox
                rect = Rectangle((minc, minr), maxc - minc, maxr - minr, fill=False, edgecolor='red', linewidth=0.3)
                axs[1, 1].add_patch(rect)
            regions = regionprops(lbl_prd_z)
            for region in regions:
                minr, minc, maxr, maxc = region.bbox
                rect = Rectangle((minc, minr), maxc - minc, maxr - minr, fill=False, edgecolor='red', linewidth=0.3)
                axs[1, 2].add_patch(rect)
            # Save the figure to a file
            plt.savefig(os.path.join("test", f"{self.params['experiment']} img {step_tst:02d} xyz {x, y, z} box.png"),
                        dpi=1200)
            plt.close()

    # load a large ct scan and segment
    def segment(self, path_img, path_lbl):
        path_dict = [{"image": path_img, "label": path_lbl}]
        # Define transforms for loading and preprocessing the NIfTI files
        transforms = Compose([
            # CropLabelledVolumed(keys=["image", "label"]),
            LoadImaged(keys=["image", "label"]),
            EnsureChannelFirstd(keys=["image", "label"]),
            # Spacingd(keys=["image", "label"], pixdim=(dx, dy, dz), mode=("bilinear", "nearest")),
            OrientationD(keys=["image", "label"], axcodes="RAS"),
            ScaleIntensityRanged(keys=["image"], a_min=self.params['intensity_min'], a_max=self.params['intensity_max'],
                                 b_min=0.0, b_max=1.0, clip=True),
        ])
        dataset = CacheDataset(data=path_dict, transform=transforms)
        loader = DataLoader(dataset)

        for step_val, batch_val in enumerate(loader):
            img_inp, lbl_inp = batch_val["img"].to(self.device), batch_val["lbl"].to(self.device)
            # run inference window
            roi_size = (128, 128, 128)
            sw_batch_size = 4
            lbl_prd = sliding_window_inference(img_inp, roi_size, sw_batch_size, self.model)
            lbl_prd = [AsDiscrete(threshold=0.5)(lbl) for lbl in decollate_batch(lbl_prd)]
            lbl_inp = [AsDiscrete(threshold=0.5)(lbl) for lbl in decollate_batch(lbl_inp)]
            # convert tensors from one hot encoding to single channel
            lbl_out = torch.argmax(lbl_prd, dim=1).cpu().numpy()[0, :, :, :]
            # nib.save(os.path.join(self.params["dir_model"], f'{self.params["experiment"]} params.dat')
            # img_array = lbl_inp.cpu().numpy()[0, 0, :, :, :]

            # lbl_array_prd = torch.argmax(prd_val, dim=1).cpu().numpy()[0, :, :, :]
            print(f"Plotting sample {step_val}...")
            # Find the (x,y,z) coordinates of the sample center
            # lbl_array_val = label(lbl_array_val, connectivity=1)
            # lbl_array_prd = label(lbl_array_prd, connectivity=1)
            # Generate overlay images

            # plt.savefig(os.path.join(self.params["dir_model"], f"{self.params['experiment']} img {step_val:02d} xyz {x, y, z} box.png"), dpi=1200)
            # plt.close()

    def roi_set_size_bak(self):
        """
        Find the volume of all brain masks in the dataset,
        and return an ROI which sides are the largest among all brain masks.
        """
        roi_max_size = np.array([0, 0, 0])
        for img, msk in {**self.dataset_trn, **self.dataset_val, **self.dataset_tst}:
            val = sitk.GetArrayViewFromImage(sitk.ReadImage(msk))
            nonzero_indices = np.nonzero(val)
            min_coord = np.min(nonzero_indices, axis=1)
            max_coord = np.max(nonzero_indices, axis=1)
            # Calculate the size of the bounding box
            roi_size = tuple(np.array(max_coord) - np.array(min_coord))
            # Calculate the center of the bounding box
            roi_center = tuple((np.array(min_coord) + np.array(max_coord)) / 2)
            print(img, roi_center, roi_size)
            # Update ROI size
            xyz_map = roi_size > roi_max_size
            roi_max_size[xyz_map] = roi_size[xyz_map]
        self.roi_size = roi_max_size.astype(int)

    def set_roi(self, n=5, x=1.2):
        """
        Get all ROI size from all information files in the dataset, and define the ROI size to use for
        training, which must include all possible ROIs and be divisible by 2^n, where n is the number
        of layers in the UNet model.
        """
        roi_size = np.zeros(3)
        for val in self.dataset_trn + self.dataset_val + self.dataset_tst:
            roi = val["roi"]
            # Read the bounding box information from the CSV file
            with open(roi, "r") as file:
                reader = csv.reader(file)
                # Skip the header row
                next(reader)
                # Read each row containing bounding box information
                for row in reader:
                    bbox_c_x, bbox_c_y, bbox_c_z, bbox_s_x, bbox_s_y, bbox_s_z = row
                    # Convert string values to integers
                    bbox_s = np.array([int(bbox_s_x), int(bbox_s_y), int(bbox_s_z)])
                    # Append bounding box information to the list
                    roi_size[bbox_s > roi_size] = bbox_s[bbox_s > roi_size]
        # Rescale the ROI
        roi_size = np.floor(roi_size * x)
        # Find the smallest ROI which is divisible by 2^n
        roi_size_x = closest_divisible_by_power_of_two(roi_size[0], n)
        roi_size_y = closest_divisible_by_power_of_two(roi_size[1], n)
        roi_size_z = closest_divisible_by_power_of_two(roi_size[2], n)
        self.roi_size = np.array([roi_size_x, roi_size_y, roi_size_z]).astype(int)

    class CropImageBasedOnROI(Transform):
        def __init__(self, img_keys: list, roi_key: str, roi_size):
            super().__init__()
            self.img_keys = img_keys
            self.roi_key = roi_key
            self.roi_size: np.array = roi_size
            self.roi_center: np.array or None = None

        def __call__(self, data):

            # Extract the roi
            roi = data[self.roi_key]
            bbox_c_x, bbox_c_y, bbox_c_z, bbox_s_x, bbox_s_y, bbox_s_z = roi
            roi_center = np.array([bbox_c_x, bbox_c_y, bbox_c_z])

            # Apply custom transformation to the input image
            cropped_img = SpatialCropD(keys=self.img_keys, roi_center=roi_center, roi_size=self.roi_size)

            # Update the data dictionary with the cropped image
            cropped_data = {self.img_keys: cropped_img}

            return cropped_data

    class LoadImageAndTextD(LoadImaged):
        def __init__(self, image_keys: list[str], text_keys: list[str]):
            super().__init__(keys=image_keys)
            self.text_keys = text_keys

        def __call__(self, data):
            data = super().__call__(data)  # Call the parent class method to load image data

            for key in self.text_keys:
                if key in data:
                    text_path = data[key]
                    if os.path.exists(text_path) and os.path.isfile(text_path):
                        with open(text_path, 'r') as file:
                            csv_reader = csv.reader(file)
                            # Skip the header row
                            next(csv_reader)
                            # Read the second row and combine the first three elements into an array
                            row = next(csv_reader)
                            roi_center = np.array(row[:3]).astype(int)
                            data[key] = roi_center
                    else:
                        raise ValueError(f"Text file not found or is not a regular file: {text_path}")
            print(data)
            return data
