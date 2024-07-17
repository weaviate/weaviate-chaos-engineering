# TODO try with and wihout async replication (other things to try?)
import itertools
import math
import subprocess
import time

weaviate_versions = [
    # "1.25.4",
    # "1.26.0-rc.0",
    # # "preview--4572237",
    # # "preview--c543408",
    # "preview--1c310dc",
    # TODO main...
    # 'local-nate-c181a94a6-v2',
]
disable_recovery_on_panics = [True] #, False]
port_to_querys = [
    # 8089,
    8088,
] #, 8087]
node_ids_to_corrupts = [1] #, 2, 3]
corruptions = [
    # 'NULL', 
    'hnsw_condensed_byte0', 
    # 'classifications_byte0',
    # 'modules_byte0',
    # 'indexcount_byte0',
    # 'segment_bloom_byte0',
    # 'segment_cna_byte0',
    # 'segment_db_byte0', 
    # 'segment_wal_byte0',
    # 'proplen_byte0', 
    # 'version_byte0',
    # 'schema_byte0',
    # 'raft_db_truncate1',
    # 'wal_overwrite', 
]
num_concurrent_corruptions = [1]
num_corruption_combos = sum([math.comb(len(corruptions), i) for i in num_concurrent_corruptions])
consistency_levels = [
    "QUORUM",
    # "ALL", 
    # "ONE",
]
num_repeats = 1
output_filepath = f'corrupt_results/{int(time.time())}'

estimated_num_runs = len(weaviate_versions) * len(disable_recovery_on_panics) * len(port_to_querys) * len(node_ids_to_corrupts) * num_corruption_combos * len(consistency_levels) * num_repeats
print('estimated_num_runs', estimated_num_runs)

so_far_num_runs = 0
progress_cadence = 10
for disable_recovery_on_panic in disable_recovery_on_panics:
    for consistency_level in consistency_levels:
        for port_to_query in port_to_querys:
            for node_to_corrupt in node_ids_to_corrupts:
                for num_corruption_to_apply in num_concurrent_corruptions:
                    for corruptions_to_apply in itertools.combinations(corruptions, num_corruption_to_apply):
                        for weaviate_version in weaviate_versions:
                            for num_repeat in range(num_repeats):
                                command = f"WEAVIATE_VERSION={weaviate_version} {'DISABLE_RECOVERY_ON_PANIC=true' if disable_recovery_on_panic else ''} ./corrupt_shards.sh {port_to_query} {node_to_corrupt} {'_'.join(corruptions_to_apply)} {consistency_level} {output_filepath}"
                                # TODO output progress
                                returncode = subprocess.call(command, shell=True)
                                print(returncode)
                                so_far_num_runs += 1
                                if so_far_num_runs % progress_cadence == 0:
                                    print(f'''


************************************************************************************************************************
*
* PROGRESS: {so_far_num_runs} of {estimated_num_runs}
*
************************************************************************************************************************

                                      ''')

# TODO include git commit next to each csv result