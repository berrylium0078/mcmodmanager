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
valid_loaders_str = ', '.join(valid_loaders)

def execute(args: argparse.Namespace):
    mc_version = args.game_version or os.getenv('MC_VERSION', '1.20.1')
    mod_loader = args.mod_loader or os.getenv('MOD_LOADER', 'forge')
    releaseType = args.release_type or "release"
    curseforge_api_key = os.getenv('CURSEFORGE_API_KEY')
    slugs: List[str] = args.mods

    # Validate mod loader
    if mod_loader.lower() not in valid_loaders:
        print(f"Error: Invalid mod loader '{mod_loader}'. Must be one of: {', '.join(valid_loaders)}")
        sys.exit(1)

    if not curseforge_api_key:
        print("Warning: CURSEFORGE_API_KEY not set. CurseForge mods will not be available.")

    searcher = ModSearcher(mc_version, mod_loader, curseforge_api_key)
    files = searcher.search_mods(slugs, releaseType)

    HOME_DIR = os.getenv('HOME') or '~'
    TARGET_DIR = HOME_DIR + '/.cache/mcmod/mods/';
    for file in files:
        file.dest = TARGET_DIR + file.dest

    if slugs:
        print(f'Error: the following mods cannot be found: {slugs}')

    for file in files:
        print(f'{file.url} ==> {file.dest}')
    if click.confirm('Note: the above mod files will be downloaded, proceed?'):
        asyncio.run(download_files(files, 5))
        pass
    else:
        print('Download canceled')

def addparser(subparsers):
    parser: argparse.ArgumentParser
    parser = subparsers.add_parser('download', help='Download Minecraft mods from CurseForge and Modrinth')
    parser.add_argument('mods', nargs='+', help='Mod IDs to download')
    parser.add_argument('--game-version', help='MineCraft version string (e.g. 1.20.1)')
    parser.add_argument('--mod-loader', help=f'Mod loader name, available options: {valid_loaders_str}')
    parser.add_argument('--release-type', help=f'Release type, default "release", other options: "beta", "alpha"');
    parser.set_defaults(func=execute)
