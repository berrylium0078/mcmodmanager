#!/usr/bin/env python3
"""
MC Mod Downloader - Download Minecraft mods from CurseForge and Modrinth
"""

import asyncio
import os
import click
import sys
import argparse
from src.lib.downloader import download_files
from src.lib.searcher import ModSearcher, mod_loader_lookup
from typing import List

valid_loaders = mod_loader_lookup.keys()
valid_release_types = set(['release', 'beta', 'alpha'])
valid_loaders_str = ', '.join(valid_loaders)

def execute(args: argparse.Namespace):
    mc_version = args.game_version or os.getenv('MC_VERSION', '1.20.1')
    mod_loader = (args.mod_loader or os.getenv('MOD_LOADER', 'forge')).lower()
    releaseType = (args.release_type or "release").lower()
    curseforge_api_key = os.getenv('CURSEFORGE_API_KEY')
    slugs: List[str] = args.mods

    # Validate mod loader
    if mod_loader.lower() not in valid_loaders:
        print(f"Error: Invalid mod loader '{mod_loader}'. Must be one of: {', '.join(valid_loaders)}")
        sys.exit(1)
    # Validate release type
    if releaseType.lower() not in valid_release_types:
        print(f"Error: Invalid release type '{releaseType}'. Must be one of: {', '.join(valid_release_types)}")
        sys.exit(1)

    if not curseforge_api_key:
        print("Warning: CURSEFORGE_API_KEY not set. CurseForge mods will not be available.")

    searcher = ModSearcher(mc_version, mod_loader, curseforge_api_key)
    files = searcher.search_mods(slugs, releaseType)

    HOME_DIR = os.getenv('HOME') or '~'
    TARGET_DIR = HOME_DIR + '/.cache/mcmod/mods/'

    if slugs:
        print(f'Warning: the following mods cannot be found: {slugs}')

    if not files:
        print(f'Nothing to do.')
        return

    filestat = [(file, os.path.exists(TARGET_DIR + file.dest)) for file in files]

    for file,stat in filestat:
        if stat:
            print(f'{file.dest} (overwrite)')
        else:
            print(f'{file.dest}')

    if any(stat for _,stat in filestat):
        if not click.confirm('Warning: some files already exists, overwrite?', default = False):
            files = [file for file,stat in filestat if not stat]
        if not files:
            print(f'Nothing to do.')
            return
    elif not click.confirm('Note: the above mod files will be downloaded, proceed?', default = True):
        print('Download canceled')
        return

    asyncio.run(download_files(files, args.parallel or 5))

def addparser(subparsers):
    parser: argparse.ArgumentParser
    parser = subparsers.add_parser('download', help='Download Minecraft mods from CurseForge and Modrinth')
    parser.add_argument('mods', nargs='+', help='Mod IDs to download')
    parser.add_argument('-g', '--game-version', help='MineCraft version string (e.g. 1.20.1)')
    parser.add_argument('-m', '--mod-loader', help=f'Mod loader name, available options: {valid_loaders_str}')
    parser.add_argument('-r', '--release-type', help=f'Release type, default "release", other options: "beta", "alpha"');
    parser.add_argument('-p', '--parallel', help=f'number of parallel threads to download, default is 5');
    parser.set_defaults(func=execute)
