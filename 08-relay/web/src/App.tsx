import { useEffect, useState } from 'react';
import { usePoll, type Overview as OverviewData, type Worker } from './api';
import { Overview } from './Overview';
import { WorkflowDetailPage, WorkflowList } from './Workflows';

function useHash(): string {
  const [hash, setHash] = useState(() => location.hash.slice(1) || '/');
  useEffect(() => {
    const on = () => setHash(location.hash.slice(1) || '/');
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);
  return hash;
}

export function App() {
  const route = useHash();
  const overview = usePoll<OverviewData>('/overview', 1000);
  const workers = usePoll<Worker[]>('/workers', 1000);
  const detail = /^\/workflows\/([0-9a-f-]{36})$/.exec(route);
  const onList = route.startsWith('/workflows');
  const waiting = overview.data?.pending_approvals.length ?? 0;

  return (
    <>
      <header className="top">
        <a className="brand" href="#/">
          <span className="baton" aria-hidden="true" />
          RELAY
        </a>
        <nav aria-label="Sections">
          <a href="#/" aria-current={!onList ? 'page' : undefined}>
            Live
          </a>
          <a href="#/workflows" aria-current={onList ? 'page' : undefined}>
            Workflows
          </a>
        </nav>
        <p className={`link ${overview.error ? 'down' : 'up'}`} role="status">
          {overview.error
            ? `Can’t reach the API: ${overview.error}. Showing the last data received.`
            : overview.data
              ? `Connected. ${waiting ? `${waiting} waiting for your decision.` : 'Nothing is waiting on you.'}`
              : 'Connecting…'}
        </p>
      </header>
      <main>
        {detail ? (
          <WorkflowDetailPage id={detail[1]!} />
        ) : onList ? (
          <WorkflowList query={route.split('?')[1] ?? ''} />
        ) : (
          <Overview overview={overview.data} workers={workers.data} refresh={overview.refresh} />
        )}
      </main>
    </>
  );
}
