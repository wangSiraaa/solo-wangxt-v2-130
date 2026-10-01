export default function ResidualPanel({ result }) {
  if (!result) return null;
  const residuals = [...(result.residuals || [])].sort((a, b) => Math.abs(b.residual_m) - Math.abs(a.residual_m)).slice(0, 50);
  const stats = result.audit?.residual_stats || {};
  return (
    <section className="card">
      <h2>改正数与残差追踪</h2>
      <div className="stat-grid">
        <div><span>方法</span><strong>{result.method}</strong></div>
        <div><span>σ0</span><strong>{Number(result.sigma0 || 0).toExponential(3)}</strong></div>
        <div><span>RMS</span><strong>{Number(stats.rms_residual_m || 0).toExponential(3)}</strong></div>
        <div><span>最大残差</span><strong>{Number(stats.max_abs_residual_m || 0).toExponential(3)}</strong></div>
        <div><span>自由度</span><strong>{result.degrees_of_freedom}</strong></div>
        <div><span>条件数估计</span><strong>{Number(result.condition_estimate || 0).toExponential(2)}</strong></div>
      </div>
      <table>
        <thead><tr><th>测段</th><th>原始均值</th><th>平差高差</th><th>改正数</th><th>残差</th></tr></thead>
        <tbody>
          {residuals.map((r) => (
            <tr key={r.observation_id}>
              <td>{r.line_code}</td>
              <td>{r.raw_mean.toFixed(6)}</td>
              <td>{r.adjusted_delta_m.toFixed(6)}</td>
              <td>{r.correction_m.toExponential(4)}</td>
              <td className={Math.abs(r.residual_m) > 0.01 ? 'bad' : ''}>{r.residual_m.toExponential(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
