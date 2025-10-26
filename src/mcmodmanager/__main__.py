import src.mcmodmanager.download as download
import src.mcmodmanager.health as health
import argparse

subcmds = ['download']

parser = argparse.ArgumentParser(description='Manage Minecraft mods')
subparsers = parser.add_subparsers(dest='command', help='subcommand')
download.addparser(subparsers)
health.addparser(subparsers)

if __name__ == '__main__':
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()
