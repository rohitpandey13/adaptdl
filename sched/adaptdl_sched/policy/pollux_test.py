# Copyright 2020 Petuum, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import pytest
import time

from collections import Counter
from datetime import datetime, timedelta
from adaptdl_sched.policy.pollux import PolluxPolicy
from adaptdl_sched.policy.utils import JobInfo, NodeInfo
from adaptdl.speedup import Params, SpeedupFunction


@pytest.mark.parametrize("num_nodes", [1, 2, 4, 8, 16])
def test_optimize(num_nodes, total_devices=16):
    assert total_devices % num_nodes == 0
    num_devices = total_devices // num_nodes
    print("{}x{} nodes:".format(num_nodes, num_devices))
    # Make up a realistic speedup function.
    params = Params(0.121, 0.00568, 0.0236, 0.00634, 0.0118, 0.00317, 1.14)
    grad_params = {"norm": 0.00136, "var": 0.000502}
    speedup_fn = SpeedupFunction(
            params, grad_params, init_batch_size=128, max_batch_size=1280,
            local_bsz_bounds=(64, 256), elastic_bsz=True)
    now = datetime.now()
    jobs = {}
    # Add a few jobs.
    job_resources = {"nvidia.com/gpu": 1, "pods": 1}
    for i in range(16):
        creation_timestamp = now + timedelta(minutes=len(jobs)),
        max_replicas = 8
        key = len(jobs)
        jobs[key] = JobInfo(job_resources, speedup_fn, creation_timestamp,
                            max_replicas)
    # Add a few nodes.
    node_resources = {"nvidia.com/gpu": num_devices, "pods": 32}
    nodes = {i: NodeInfo(node_resources, preemptible=False)
             for i in range(num_nodes)}
    # Add a node template.
    node_template = NodeInfo(node_resources, preemptible=True)
    policy = PolluxPolicy()
    prev_allocs = {}
    for i in range(3):
        start = time.time()
        allocations, desired_nodes = \
            policy.optimize(jobs, nodes, prev_allocs, node_template)
        duration = time.time() - start
        print("optimize {}x ({}s sec):".format(i + 1, duration))
        node_count = Counter()
        for job_key, placement in allocations.items():
            assert len(placement) <= jobs[job_key].max_replicas
            for node_key in placement:
                node_count[node_key] += 1
        for node_key, count in node_count.items():
            assert count <= nodes[node_key].resources["nvidia.com/gpu"]
            assert count <= nodes[node_key].resources["pods"]
