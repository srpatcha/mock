#!/usr/bin/python -tt
# vim:expandtab:autoindent:tabstop=4:shiftwidth=4:filetype=python:textwidth=0:
# Originally written by Seth Vidal
# Sections taken from Mach by Thomas Vander Stichele
# Major reorganization and adaptation by Michael Brown
# Copyright (C) 2007 Michael E Brown <mebrown@michaels-house.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

# library imports
import grp
import logging
import logging.config
import os
import os.path
import sys
import time
from optparse import OptionParser

# all of the variables below are substituted by the build system
__VERSION__="0.8.2"
SYSCONFDIR="/usr/local/etc"
PYTHONDIR="/usr/local/lib/python2.5/site-packages"
PKGPYTHONDIR="/usr/local/lib/python2.5/site-packages/mock"
MOCKCONFDIR= SYSCONFDIR + "/mock"

# import all mock.* modules after this.
sys.path.insert(0,PYTHONDIR)

# our imports
import mock.exception
from mock.trace_decorator import traceLog
import mock.backend
import mock.uid
import mock.util

# set up basic logging until config file can be read
log = logging.getLogger()
logging.basicConfig()

@traceLog(log)
def command_parse(config_opts):
    """return options and args from parsing the command line"""
    
    usage = """
    usage:
           mock [options] {init|clean}
           mock [options] [rebuild] /path/to/srpm(s)
           mock [options] {shell|chroot} <cmd>
           mock [options] installdeps {SRPM|RPM}
           mock [options] install PACKAGE
    commands: 
        rebuild     - build the specified SRPM(s) [default command]
        chroot      - run the specified command within the chroot
        shell       - run an interactive shell within specified chroot
        clean       - clean out the specified chroot
        init        - initialize the chroot, do not build anything
        installdeps - install build dependencies for a specified SRPM
        install     - install packages using yum"""

    parser = OptionParser(usage=usage, version=__VERSION__)
    parser.add_option("-r", action="store", type="string", dest="chroot",
                      help="chroot name/config file name default: %default", 
                      default='default')
    parser.add_option("--no-clean", action ="store_false", dest="clean", 
                      help="do not clean chroot before building", default=True)
    parser.add_option("--cleanup-after", action ="store_true", dest="cleanup_after", 
                      help="Clean chroot after building. Use with --resultdir. Only active for 'rebuild'.", default=None)
    parser.add_option("--no-cleanup-after", action ="store_false", dest="cleanup_after", 
                      help="Dont clean chroot after building. If automatic cleanup is enabled, use this to disable.", default=None)
    parser.add_option("--arch", action ="store", dest="arch", 
                      default=None, help="target build arch")
    parser.add_option("--resultdir", action="store", type="string", 
                      default=None, help="path for resulting files to be put")
    parser.add_option("--uniqueext", action="store", type="string", default=None,
                      help="Arbitrary, unique extension to append to buildroot directory name")
    parser.add_option("--configdir", action="store", dest="configdir", default=None,
                      help="Change where config files are found")
    parser.add_option("--rpmbuild_timeout", action="store", dest="rpmbuild_timeout", type="int",
                      default=None, help="Fail build if rpmbuild takes longer than 'timeout' seconds ")

    # caching
    parser.add_option("--enable-plugin", action="append", dest="enabled_plugins", type="string",
                      default=[], help="Enable plugin. Currently-available plugins: %s" % repr(config_opts['plugins']))
    parser.add_option("--disable-plugin", action="append", dest="disabled_plugins", type="string",
                      default=[], help="Disable plugin. Currently-available plugins: %s" % repr(config_opts['plugins']))
    
    return parser.parse_args()

@traceLog(log)
def setup_default_config_opts(config_opts):
    # global
    config_opts['basedir'] = '/var/lib/mock/' # root name is automatically added to this
    config_opts['cache_topdir'] = '/var/lib/mock/cache'
    config_opts['clean'] = True
    config_opts['chroothome'] = '/builddir'
    config_opts['log_config_file'] = 'logging.ini'
    config_opts['rpmbuild_timeout'] = 0
    config_opts['chrootuser'] = 'mockbuild'
    config_opts['chrootgroup'] = 'mockbuild'
    config_opts['chrootuid'] = os.getuid()
    try:
        config_opts['chrootgid'] = grp.getgrnam("mock")[2]
    except KeyError:
        #  'mock' group doesnt exist, must set in config file
        pass

    # cleanup_on_* only take effect for separate --resultdir
    # config_opts provides fine-grained control. cmdline only has big hammer
    config_opts['cleanup_on_success'] = 1
    config_opts['cleanup_on_failure'] = 1

    # (global) plugins and plugin configs
    config_opts['plugins'] = ('ccache', 'yum_cache', 'root_cache', 'bind_mount')
    config_opts['plugin_dir'] = os.path.join(PKGPYTHONDIR, "plugins")
    config_opts['plugin_conf'] = {
            'ccache_enable': True,
            'ccache_opts': {'max_age_days': 15, 'max_cache_size': "4G"},
            'yum_cache_enable': True,
            'yum_cache_opts': {'max_age_days': 15},
            'root_cache_enable': True,
            'root_cache_opts': {'max_age_days': 15},
            'bind_mount_enable': True,
            'bind_mount_opts': {'dirs': [
                        # specify like this:
                        # ( '/host/path', '/bind/mount/path/in/chroot/' ),
                        # ( '/another/host/path', '/another/bind/mount/path/in/chroot/' ),
                    ]},
            }

    # dependent on guest OS
    config_opts['use_host_resolv'] = True
    config_opts['chroot_setup_cmd'] = 'install buildsys-build'
    config_opts['target_arch'] = 'i386'
    config_opts['yum.conf'] = ''
    config_opts['more_buildreqs'] = {}
    config_opts['files'] = {}
    config_opts['files']['etc/hosts'] = "127.0.0.1 localhost localhost.localdomain\n"
    config_opts['macros'] = {'%_topdir': '%s/build' % config_opts['chroothome'],
                             '%_rpmfilename': '%%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm',
                             }

@traceLog(log)
def set_config_opts_per_cmdline(config_opts, options):
    # do some other options and stuff
    if options.arch:
        config_opts['target_arch'] = options.arch
    if not options.clean:
        config_opts['clean'] = options.clean

    if options.resultdir:
        config_opts['resultdir'] = options.resultdir
    if options.uniqueext:
        config_opts['unique-ext'] = options.uniqueext
    if options.rpmbuild_timeout is not None:
        config_opts['rpmbuild_timeout'] = options.rpmbuild_timeout

    for i in options.disabled_plugins:
        if i not in config_opts['plugins']:
            raise mock.exception.BadCmdline("Bad option for '--disable-plugins=%s'. Expecting one of: %s" % (i, config_opts['plugins']))
        config_opts['plugin_conf']['%s_enable' % i] = False
    for i in options.enabled_plugins:
        if i not in config_opts['plugins']:
            raise mock.exception.BadCmdline("Bad option for '--enable-plugins=%s'. Expecting one of: %s" % (i, config_opts['plugins']))
        config_opts['plugin_conf']['%s_enable' % i] = True

    if options.cleanup_after and not options.resultdir:
        raise mock.exception.BadCmdline("Must specify --resultdir when using --cleanup-after")

    if options.cleanup_after == False:
        config_opts['cleanup_on_success'] = False
        config_opts['cleanup_on_failure'] = False

    if options.cleanup_after == True:
        config_opts['cleanup_on_success'] = True
        config_opts['cleanup_on_failure'] = True

    # cant cleanup unless separate resultdir
    if not options.resultdir:
        config_opts['cleanup_on_success'] = False
        config_opts['cleanup_on_failure'] = False

@traceLog(log)
def warn_obsolete_config_options(config_opts):
    pass

@traceLog(log)
def do_rebuild(config_opts, chroot, srpms):
    if len(srpms) < 1:
        log.critical("No package specified to rebuild command.")
        sys.exit(50)

    # check that everything is kosher. Raises exception on error
    for hdr in mock.util.yieldSrpmHeaders(srpms): pass

    try:
        for srpm in srpms:
            start = time.time()
            log.info("Start(%s)" % srpm)
            if config_opts['clean'] and chroot.state() != "clean":
                chroot.clean()
            chroot.init()
            chroot.build(srpm, timeout=config_opts['rpmbuild_timeout'])
            elapsed = time.time() - start
            log.info("Done(%s)  %d minutes %d seconds" % (srpm, elapsed//60, elapsed%60))
            log.info("Results and/or logs in: %s" % chroot.resultdir)
    
        if config_opts["cleanup_on_success"]:
            log.info("Cleaning up build root ('clean_on_success=True')")
            chroot.clean()
    except (Exception, KeyboardInterrupt), e:
        if config_opts["cleanup_on_failure"]:
            log.error("Got exception. Cleaning up build root ('clean_on_failure=True')")
            chroot.clean()
        raise

def main(retParams):
    # defaults
    config_opts = {}
    setup_default_config_opts(config_opts)
    (options, args) = command_parse(config_opts)
    
    # config path -- can be overridden on cmdline
    config_path=MOCKCONFDIR
    if options.configdir:
        config_path = options.configdir

    # basic config for logging until config files are read
    logging.config.fileConfig(os.path.join(config_path, config_opts["log_config_file"]))

    # check args
    if len(args) < 1:
        log.error("No srpm or command specified - nothing to do")
        sys.exit(50)

    # Read in the config files: default, and then user specified
    for cfg in ( os.path.join(config_path, 'defaults.cfg'), '%s/%s.cfg' % (config_path, options.chroot)):
        if os.path.exists(cfg):
            execfile(cfg)
        else:
            log.error("Could not find required config file: %s" % cfg)
            if options.chroot == "default": log.error("  Did you forget to specify the chroot to use with '-r'?")
            sys.exit(1)
    
    # reconfigure logging in case config file was overridden
    logging.config.fileConfig(os.path.join(config_path, config_opts["log_config_file"]))

    # cmdline options override config options
    log.info("mock.py version %s starting..." % __VERSION__)
    set_config_opts_per_cmdline(config_opts, options)
    warn_obsolete_config_options(config_opts)

    # do whatever we're here to do
    #   uidManager saves current real uid/gid which are unpriviledged (callers)
    #   due to suid helper, our current effective uid is 0
    uidManager = mock.uid.uidManager(os.getuid(), os.getgid())
    chroot = mock.backend.Root(config_opts, uidManager)

    # elevate privs
    uidManager.becomeUser(0)

    retParams["chroot"] = chroot
    retParams["config_opts"] = config_opts
    os.umask(002)
    if config_opts['clean']:
        chroot.clean()

    if args[0] == 'init':
        chroot.init()

    elif args[0] == 'clean':
        if chroot.state() != "clean":
            chroot.clean()

    elif args[0] in ('chroot', 'shell'):
        chroot.init()
        chroot._mountall()
        try:
            cmd = ' '.join(args[1:])
            os.system("PS1='mock-chroot> ' /usr/sbin/chroot %s %s" % (chroot.rootdir, cmd))
        finally:
            chroot._umountall()

    elif args[0] == 'installdeps':
        if len(args) > 1:
            srpms = args[1:]
        else:
            log.critical("You must specify an SRPM file.")
            sys.exit(50)

        for hdr in mock.util.yieldSrpmHeaders(srpms, plainRpmOk=1): pass
        chroot.init()
        chroot._mountall()
        try:
            chroot.installSrpmDeps(*srpms)
        finally:
            chroot._umountall()

    elif args[0] == 'install':
        if len(args) > 1:
            srpms = args[1:]
        else:
            log.critical("You must specify a package list to install.")
            sys.exit(50)

        chroot.init()
        chroot.yumInstall(*srpms)

    elif args[0] == 'rebuild':
        do_rebuild(config_opts, chroot, args[1:])

    else:
        log.error("Unknown command specified: %s" % args[0])


if __name__ == '__main__':
    exitStatus = 0
    killOrphans = 1
    try:
        # sneaky way to ensure that we get passed back parameter even if 
        # we hit an exception.
        retParams = {}
        main(retParams)

    except (KeyboardInterrupt,), e:
        exitStatus = 7
        log.error("Exiting on user interrupt, <CTRL>-C")

    except (mock.exception.BadCmdline), e:
        exitStatus = e.resultcode
        log.error(str(e))
        killOrphans = 0

    except (mock.exception.BuildRootLocked), e:
        exitStatus = e.resultcode
        log.error(str(e))
        killOrphans = 0

    except (mock.exception.Error), e:
        exitStatus = e.resultcode
        log.error(str(e))

    except (Exception,), e:
        exitStatus = 1
        logging.exception(e)

    if killOrphans and retParams:
        mock.util.orphansKill(retParams["chroot"].rootdir)

    logging.shutdown()
    sys.exit(exitStatus)


