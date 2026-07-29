# coding: utf-8

"""
 Copyright 2023 TikTok Pte. Ltd.

 This source code is licensed under the MIT license found in
 the LICENSE file in the root directory of this source tree.
"""

SDK_VERSION = "1.2.1"


def get_sdk_version():
    version = SDK_VERSION

    if not version:
        raise ValueError('Cannot find version information')

    return version
