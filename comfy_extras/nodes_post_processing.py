import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageColor
import re

import comfy.utils


class Blend:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image1": ("IMAGE",),
                "image2": ("IMAGE",),
                "blend_factor": ("FLOAT", {
                    "default": 0.5,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01
                }),
                "blend_mode": (["normal", "multiply", "screen", "overlay", "soft_light"],),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "blend_images"

    CATEGORY = "image/postprocessing"

    def blend_images(self, image1: torch.Tensor, image2: torch.Tensor, blend_factor: float, blend_mode: str):
        if image1.shape != image2.shape:
            image2 = image2.permute(0, 3, 1, 2)
            image2 = comfy.utils.common_upscale(image2, image1.shape[2], image1.shape[1], upscale_method='bicubic', crop='center')
            image2 = image2.permute(0, 2, 3, 1)

        blended_image = self.blend_mode(image1, image2, blend_mode)
        blended_image = image1 * (1 - blend_factor) + blended_image * blend_factor
        blended_image = torch.clamp(blended_image, 0, 1)
        return (blended_image,)

    def blend_mode(self, img1, img2, mode):
        if mode == "normal":
            return img2
        elif mode == "multiply":
            return img1 * img2
        elif mode == "screen":
            return 1 - (1 - img1) * (1 - img2)
        elif mode == "overlay":
            return torch.where(img1 <= 0.5, 2 * img1 * img2, 1 - 2 * (1 - img1) * (1 - img2))
        elif mode == "soft_light":
            return torch.where(img2 <= 0.5, img1 - (1 - 2 * img2) * img1 * (1 - img1), img1 + (2 * img2 - 1) * (self.g(img1) - img1))
        else:
            raise ValueError(f"Unsupported blend mode: {mode}")

    def g(self, x):
        return torch.where(x <= 0.25, ((16 * x - 12) * x + 4) * x, torch.sqrt(x))

class Blur:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "blur_radius": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 31,
                    "step": 1
                }),
                "sigma": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.1,
                    "max": 10.0,
                    "step": 0.1
                }),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "blur"

    CATEGORY = "image/postprocessing"

    def gaussian_kernel(self, kernel_size: int, sigma: float):
        x, y = torch.meshgrid(torch.linspace(-1, 1, kernel_size), torch.linspace(-1, 1, kernel_size), indexing="ij")
        d = torch.sqrt(x * x + y * y)
        g = torch.exp(-(d * d) / (2.0 * sigma * sigma))
        return g / g.sum()

    def blur(self, image: torch.Tensor, blur_radius: int, sigma: float):
        if blur_radius == 0:
            return (image,)

        batch_size, height, width, channels = image.shape

        kernel_size = blur_radius * 2 + 1
        kernel = self.gaussian_kernel(kernel_size, sigma).repeat(channels, 1, 1).unsqueeze(1)

        image = image.permute(0, 3, 1, 2) # Torch wants (B, C, H, W) we use (B, H, W, C)
        blurred = F.conv2d(image, kernel, padding=kernel_size // 2, groups=channels)
        blurred = blurred.permute(0, 2, 3, 1)

        return (blurred,)

class Quantize:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "colors": ("INT", {
                    "default": 256,
                    "min": 1,
                    "max": 256,
                    "step": 1
                }),
                "dither": (["none", "floyd-steinberg"],),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "quantize"

    CATEGORY = "image/postprocessing"

    def quantize(self, image: torch.Tensor, colors: int = 256, dither: str = "FLOYDSTEINBERG"):
        batch_size, height, width, _ = image.shape
        result = torch.zeros_like(image)

        dither_option = Image.Dither.FLOYDSTEINBERG if dither == "floyd-steinberg" else Image.Dither.NONE

        for b in range(batch_size):
            tensor_image = image[b]
            img = (tensor_image * 255).to(torch.uint8).numpy()
            pil_image = Image.fromarray(img, mode='RGB')

            palette = pil_image.quantize(colors=colors) # Required as described in https://github.com/python-pillow/Pillow/issues/5836
            quantized_image = pil_image.quantize(colors=colors, palette=palette, dither=dither_option)

            quantized_array = torch.tensor(np.array(quantized_image.convert("RGB"))).float() / 255
            result[b] = quantized_array

        return (result,)

class Sharpen:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "sharpen_radius": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 31,
                    "step": 1
                }),
                "alpha": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.1,
                    "max": 5.0,
                    "step": 0.1
                }),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "sharpen"

    CATEGORY = "image/postprocessing"

    def sharpen(self, image: torch.Tensor, sharpen_radius: int, alpha: float):
        if sharpen_radius == 0:
            return (image,)

        batch_size, height, width, channels = image.shape

        kernel_size = sharpen_radius * 2 + 1
        kernel = torch.ones((kernel_size, kernel_size), dtype=torch.float32) * -1
        center = kernel_size // 2
        kernel[center, center] = kernel_size**2
        kernel *= alpha
        kernel = kernel.repeat(channels, 1, 1).unsqueeze(1)

        tensor_image = image.permute(0, 3, 1, 2) # Torch wants (B, C, H, W) we use (B, H, W, C)
        sharpened = F.conv2d(tensor_image, kernel, padding=center, groups=channels)
        sharpened = sharpened.permute(0, 2, 3, 1)

        result = torch.clamp(sharpened, 0, 1)

        return (result,)

class Transpose:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "method": ([
                    "Flip horizontal",
                    "Flip vertical",
                    "Rotate 90°",
                    "Rotate 180°",
                    "Rotate 270°",
                    "Transpose",
                    "Transverse",
                ],),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "transpose"

    CATEGORY = "image/postprocessing"

    def transpose(self, image: torch.Tensor, method: str):
        batch_size, height, width, _ = image.shape
        result = torch.zeros_like(image)

        methods = {
            "Flip horizontal": Image.Transpose.FLIP_LEFT_RIGHT,
            "Flip vertical": Image.Transpose.FLIP_TOP_BOTTOM,
            "Rotate 90°": Image.Transpose.ROTATE_90,
            "Rotate 180°": Image.Transpose.ROTATE_180,
            "Rotate 270°": Image.Transpose.ROTATE_270,
            "Transpose": Image.Transpose.TRANSPOSE,
            "Transverse": Image.Transpose.TRANSVERSE,
        }

        for b in range(batch_size):
            tensor_image = image[b]
            img = (tensor_image * 255).to(torch.uint8).numpy()
            pil_image = Image.fromarray(img, mode='RGB')

            transposed_image = pil_image.transpose(methods[method])

            transposed_array = torch.tensor(np.array(transposed_image.convert("RGB"))).float() / 255
            result[b] = transposed_array

        return (result,)

class Rotate:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "angle": ("FLOAT", {
                    "default": 0, 
                    "min": 0,
                    "max": 360,
                    "step": 0.1
                }),
                "resample": ([
                    "Nearest Neighbor",
                    "Bilinear",
                    "Bicubic",
                ],),
                "fill_color": ("STRING", {"default": "#000000"}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "rotate"

    CATEGORY = "image/postprocessing"

    def rotate(self, image: torch.Tensor, angle: int, resample: str, fill_color: str):
        batch_size, height, width, _ = image.shape
        result = torch.zeros_like(image)

        resamplers = {
            "Nearest Neighbor": Image.Resampling.NEAREST,
            "Bilinear": Image.Resampling.BILINEAR,
            "Bicubic": Image.Resampling.BICUBIC,
        }

        for b in range(batch_size):
            tensor_image = image[b]
            img = (tensor_image * 255).to(torch.uint8).numpy()
            pil_image = Image.fromarray(img, mode='RGB')
            
            fill_color = fill_color or "#000000"

            def parse_palette(color_str):
                if re.match(r'^#[a-fA-F0-9]{6}$', color_str) or color_str.lower() in ImageColor.colormap:
                    return ImageColor.getrgb(color_str)

                color_rgb = re.match(r'^\(?(\d{1,3}),(\d{1,3}),(\d{1,3})\)?$', color_str)
                if color_rgb and int(color_rgb.group(1)) <= 255 and int(color_rgb.group(2)) <= 255 and int(color_rgb.group(3)) <= 255:
                    return tuple(map(int, re.findall(r'\d{1,3}', color_str)))
                else:
                    raise ValueError(f"Invalid color format: {color_str}")

            color = fill_color.replace(" ", "")
            color = parse_palette(color)
            rotated_image = pil_image.rotate(angle=angle, resample=resamplers[resample], expand=False, fillcolor=color)

            rotated_array = torch.tensor(np.array(rotated_image.convert("RGB"))).float() / 255
            result[b] = rotated_array

        return (result,)

NODE_CLASS_MAPPINGS = {
    "ImageBlend": Blend,
    "ImageBlur": Blur,
    "ImageQuantize": Quantize,
    "ImageSharpen": Sharpen,
    "ImageTranspose": Transpose,
    "ImageRotate": Rotate,
}
