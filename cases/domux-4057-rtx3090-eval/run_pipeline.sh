#!/usr/bin/env bash
set -euo pipefail

case_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${case_dir}/../.." && pwd)"
artifacts_dir="${case_dir}/artifacts"
raw_dir="${artifacts_dir}/raw"
mkdir -p "${raw_dir}"

cd "${repo_root}"
"${case_dir}/collect_environment.sh" > "${artifacts_dir}/environment.txt"

python "${case_dir}/smoke_test.py" 2>&1 | tee "${raw_dir}/smoke_console.log"
python "${case_dir}/run_official_eval.py" 2>&1 | tee "${raw_dir}/eval_console.log"
python "${case_dir}/analyze_failures.py" 2>&1 | tee "${raw_dir}/analysis_console.log"
python "${case_dir}/benchmark_latency.py" 2>&1 | tee "${raw_dir}/latency_console.log"

for log_name in smoke_console eval_console analysis_console latency_console; do
  python "${case_dir}/sanitize_file.py" \
    "${raw_dir}/${log_name}.log" \
    "${artifacts_dir}/${log_name}.txt"
done

echo "Pipeline complete. Review public artifacts under ${artifacts_dir}."
