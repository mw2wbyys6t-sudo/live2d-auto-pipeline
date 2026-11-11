import type { NextPage } from 'next';
import { WorkbenchProvider } from '../lib/workbench-context';
import { PreviewProvider } from '../lib/preview-runtime';
import Workbench from '../components/workbench/Workbench';

/** 工作台本体。启动页在 /，这里是真正的三栏编辑界面。 */
const StudioPage: NextPage = () => (
  <WorkbenchProvider>
    <PreviewProvider>
      <Workbench />
    </PreviewProvider>
  </WorkbenchProvider>
);

export default StudioPage;
