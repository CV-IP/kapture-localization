#!/usr/bin/env python3
# Copyright 2020-present NAVER Corp. Under BSD 3-clause license

import os
import argparse
import logging
from typing import List, Optional

import path_to_kapture_localization  # noqa: F401
import kapture_localization.utils.logging
import kapture_localization.colmap.colmap_command as colmap_lib

import kapture_localization.utils.path_to_kapture  # noqa: F401
import kapture
import kapture.utils.logging
from kapture.io.csv import kapture_from_dir
from kapture.utils.paths import safe_remove_file
from kapture.converter.colmap.database import COLMAPDatabase
from kapture.converter.colmap.import_colmap_database import get_images_and_trajectories_from_database
from kapture.converter.colmap.import_colmap_database import get_matches_from_database
from kapture.core.Trajectories import rigs_remove_inplace
import kapture.converter.colmap.database_extra as database_extra

logger = logging.getLogger('run_colmap_gv')


def run_colmap_gv(kapture_none_matches_dirpath: str,
                  kapture_colmap_matches_dirpath: str,
                  colmap_binary: str,
                  pairsfile_path: Optional[str],
                  skip_list: List[str],
                  force: bool):
    kapture_none_matches = kapture_from_dir(kapture_none_matches_dirpath, pairsfile_path)
    kapture_colmap_matches = kapture_from_dir(kapture_colmap_matches_dirpath, pairsfile_path)
    run_colmap_gv_from_loaded_data(kapture_none_matches,
                                   kapture_colmap_matches,
                                   kapture_none_matches_dirpath,
                                   kapture_colmap_matches_dirpath,
                                   colmap_binary,
                                   skip_list,
                                   force)


def run_colmap_gv_from_loaded_data(kapture_none_matches: kapture.Kapture,
                                   kapture_colmap_matches: kapture.Kapture,
                                   kapture_none_matches_dirpath: str,
                                   kapture_colmap_matches_dirpath: str,
                                   colmap_binary: str,
                                   skip_list: List[str],
                                   force: bool):
    logger.info('run_colmap_gv...')
    if not (kapture_none_matches.records_camera and kapture_none_matches.sensors and
            kapture_none_matches.keypoints and kapture_none_matches.matches):
        raise ValueError('records_camera, sensors, keypoints, matches are mandatory')

    # COLMAP does not fully support rigs.
    if kapture_none_matches.rigs is not None and kapture_none_matches.trajectories is not None:
        # make sure, rigs are not used in trajectories.
        logger.info('remove rigs notation.')
        rigs_remove_inplace(kapture_none_matches.trajectories, kapture_none_matches.rigs)

    # Set fixed name for COLMAP database
    colmap_db_path = os.path.join(kapture_colmap_matches_dirpath, 'colmap.db')
    if 'delete_existing' not in skip_list:
        safe_remove_file(colmap_db_path, force)

    if 'matches_importer' not in skip_list:
        logger.debug('compute matches difference.')
        if kapture_colmap_matches.matches is not None:
            colmap_matches = kapture_colmap_matches.matches
        else:
            colmap_matches = kapture.Matches()
        matches_to_verify = kapture.Matches(kapture_none_matches.matches.difference(colmap_matches))
        kapture_data_to_export = kapture.Kapture(sensors=kapture_none_matches.sensors,
                                                 trajectories=kapture_none_matches.trajectories,
                                                 records_camera=kapture_none_matches.records_camera,
                                                 keypoints=kapture_none_matches.keypoints,
                                                 matches=matches_to_verify)
        # creates a new database with matches
        logger.debug('export matches difference to db.')
        colmap_db = COLMAPDatabase.connect(colmap_db_path)
        database_extra.kapture_to_colmap(kapture_data_to_export, kapture_none_matches_dirpath, colmap_db,
                                         export_two_view_geometry=False)
        # close db before running colmap processes in order to avoid locks
        colmap_db.close()

        logger.debug('run matches_importer command.')
        colmap_lib.run_matches_importer_from_kapture(
            colmap_binary,
            colmap_use_cpu=True,
            colmap_gpu_index=None,
            colmap_db_path=colmap_db_path,
            kapture_data=kapture_data_to_export,
            force=force
        )

    if 'import' not in skip_list:
        logger.debug('import verified matches.')
        os.umask(0o002)
        colmap_db = COLMAPDatabase.connect(colmap_db_path)
        kapture_data = kapture.Kapture()
        kapture_data.records_camera, _ = get_images_and_trajectories_from_database(colmap_db)
        kapture_data.matches = get_matches_from_database(colmap_db, kapture_data.records_camera,
                                                         kapture_colmap_matches_dirpath,
                                                         no_geometric_filtering=False)
        colmap_db.close()

    if 'delete_db' not in skip_list:
        logger.debug('delete intermediate colmap db.')
        os.remove(colmap_db_path)


def run_colmap_gv_command_line():
    parser = argparse.ArgumentParser(description='Run colmap matches importer and import results to kapture.',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser_verbosity = parser.add_mutually_exclusive_group()
    parser_verbosity.add_argument('-v', '--verbose', nargs='?', default=logging.WARNING, const=logging.INFO,
                                  action=kapture.utils.logging.VerbosityParser,
                                  help='verbosity level (debug, info, warning, critical, ... or int value) [warning]')
    parser_verbosity.add_argument('-q', '--silent', '--quiet',
                                  action='store_const', dest='verbose', const=logging.CRITICAL)
    parser.add_argument('-f', '-y', '--force', action='store_true', default=False,
                        help='silently delete database if already exists.')
    parser.add_argument('-i', '--input', required=True,
                        help='input path to kapture data root directory that contains non verified matches')
    parser.add_argument('-o', '--output', required=True,
                        help='input path to kapture data root directory that contains colmap gv matches')
    parser.add_argument('--pairsfile-path', default=None, type=str,
                        help='text file which contains the image pairs to load')
    parser.add_argument('-colmap', '--colmap_binary', required=False,
                        default="colmap",
                        help='full path to colmap binary '
                        '(default is "colmap", i.e. assume the binary'
                                ' is in the user PATH).')
    parser.add_argument('-s', '--skip', choices=['delete_existing',
                                                 'matches_importer',
                                                 'import',
                                                 'delete_db'],
                        nargs='+', default=[],
                        help='steps to skip')
    args = parser.parse_args()

    logger.setLevel(args.verbose)
    logging.getLogger('colmap').setLevel(args.verbose)
    if args.verbose <= logging.DEBUG:
        # also let kapture express its logs
        kapture.utils.logging.getLogger().setLevel(args.verbose)
        kapture_localization.utils.logging.getLogger().setLevel(args.verbose)

    args_dict = vars(args)
    logger.debug('run_colmap_gv.py \\\n' + ''.join(['\n\t{:13} = {}'.format(k, v) for k, v in args_dict.items()]))
    run_colmap_gv(args.input, args.output, args.colmap_binary, args.pairsfile_path, args.skip, args.force)


if __name__ == '__main__':
    run_colmap_gv_command_line()
