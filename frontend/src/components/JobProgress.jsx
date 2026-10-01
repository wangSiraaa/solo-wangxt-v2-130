const STAGES = ['accepted', 'components', 'partition_qc', 'global_solve', 'audit', 'ready'];

export default function JobProgress({ job, result }) {
  const index = STAGES.indexOf(job.stage);
  const failed = job.status === 'failed';
  const superseded = job.status === 'superseded';

  return (
    <section className="card">
      <h2>任务代次 {job.generation}</h2>
      <p>
        <strong>{job.status}</strong> / {job.stage} / 已确认：{job.confirmed_stage} / attempt {job.attempt}
      </p>
      <div className="steps">
        {STAGES.map((stage, i) => (
          <div key={stage} className={`step ${i <= index ? 'active' : ''} ${failed && i === index ? 'failed' : ''}`}>
            {stage}
          </div>
        ))}
      </div>
      {failed && <pre className="error">{job.error_code}: {job.error_message}</pre>}
      {superseded && <p className="warning">旧快照任务可审计，但已不是当前草稿，不能发布覆盖新成果。</p>}
      {result?.nullspace && (
        <div className="error-block">
          <h3>QR/秩亏诊断</h3>
          <pre>{JSON.stringify(result.nullspace, null, 2)}</pre>
        </div>
      )}
      {result?.audit?.checks && (
        <ul className="checks">
          {result.audit.checks.map((check) => (
            <li key={check.name} className={check.passed ? 'pass' : check.blocking ? 'fail' : 'warn'}>
              {check.passed ? '✓' : '✗'} {check.name}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
