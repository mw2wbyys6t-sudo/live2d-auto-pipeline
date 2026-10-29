import type { NextPage } from 'next';
import { WorkbenchProvider } from '../lib/workbench-context';
import { PreviewProvider } from '../lib/preview-runtime';
import Workbench from '../components/workbench/Workbench';

const HomePage: NextPage = () => (
  <WorkbenchProvider>
    <PreviewProvider>
      <Workbench />
    </PreviewProvider>
  </WorkbenchProvider>
);

export default HomePage;
