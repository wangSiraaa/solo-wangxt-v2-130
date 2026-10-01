import { useEffect, useMemo, useState } from 'react';
import { api } from './api/client';
import JobProgress from './components/JobProgress';
import NetworkGraph from './components/NetworkGraph';
import ResidualPanel from './components/ResidualPanel';
import './styles.css';

export default function App() {
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState('');
  const [project, setProject] = useState(null);
  const [topology, setTopology] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [result, setResult] = useState(null);
  const [file, setFile] = useState(null);
  const [message, setMessage] = useState('');

  async function refresh() {
    const list = await api.projects();
    setProjects(list);
    if (!projectId && list[0]) setProjectId(list[0].id);
    if (projectId) {
      const current = list.find((p) => p.id === projectId);
      setProject(current);
      const [topo, jobList] = await Promise.all([api.topology(projectId), api.jobs(projectId)]);
      setTopology(topo);
      setJobs(jobList);
      const job = selectedJob ? jobList.find((j) => j.id === selectedJob.id) : jobList[0];
      if (job) {
        setSelectedJob(job);
        if (['ready', 'failed', 'superseded'].includes(job.status)) {
          api.result(job.id).then(setResult).catch(() => setResult(null));
        }
      }
    }
  }

  useEffect(() => { refresh().catch((e) => setMessage(e.message)); }, [projectId]);
  useEffect(() => {
    const timer = setInterval(() => refresh().catch(console.error), 3000);
    return () => clearInterval(timer);
  }, [projectId, selectedJob?.id]);

  const problematic = useMemo(() => (topology?.components || []).filter((c) => c.datum_count === 0), [topology]);

  async function createProject() {
    const code = prompt('项目代码');
    if (!code) return;
    const p = await api.createProject(code, prompt('项目名称') || code);
    setProjectId(p.id);
    await refresh();
  }

  async function upload() {
    if (!file) return;
    const out = await api.importCsv(projectId, file);
    setMessage(`导入 ${out.created_observations} 条，草稿版本 ${out.draft_version}`);
    await refresh();
  }

  async function submit(algorithm) {
    const job = await api.submitJob(projectId, project.draft_version, algorithm);
    setMessage(`已绑定快照 ${job.snapshot_id}，任务 ${job.id}`);
    await api.advance(job.id).catch(() => null);
    await refresh();
  }

  async function publish() {
    await api.publish(projectId, project.draft_version);
    setMessage('发布成功；原始高差仍保留在观测与快照表');
  }

  return (
    <main>
      <header>
        <h1>省级水准网成果平台</h1>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.code} / v{p.draft_version}</option>)}
        </select>
        <button onClick={createProject}>新建项目</button>
      </header>

      {message && <div className="toast">{message}</div>}
      {problematic.length > 0 && <div className="error-banner">发现 {problematic.length} 个无基准连通分量，见下方拓扑。</div>}

      <div className="toolbar card">
        <input type="file" accept=".csv" onChange={(e) => setFile(e.target.files[0])} />
        <button onClick={upload}>导入 CSV</button>
        <button disabled={!project} onClick={() => submit('auto')}>提交整体求解</button>
        <button disabled={!project} onClick={() => submit('qr')}>强制 QR 诊断</button>
        <button disabled={!selectedJob || selectedJob.status !== 'ready'} onClick={publish}>发布成果</button>
      </div>

      <div className="grid">
        <section className="card graph-card">
          <h2>测点拓扑 / 连通分量</h2>
          <NetworkGraph topology={topology} />
          <div className="components">
            {(topology?.components || []).map((c) => (
              <div key={c.index} className={c.datum_count ? 'component' : 'component bad'}>
                分量 {c.index}: {c.point_count} 点 / {c.observation_count} 测段 / {c.datum_count} 基准
              </div>
            ))}
          </div>
        </section>
        <div>
          {selectedJob && <JobProgress job={selectedJob} result={result} />}
        </div>
      </div>
      <ResidualPanel result={result} />
    </main>
  );
}
