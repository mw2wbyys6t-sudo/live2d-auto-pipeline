import { useEffect } from 'react';
import { useRouter } from 'next/router';
import { Loader2 } from 'lucide-react';
import type { StageId } from '../lib/workbench';

/** 旧的多页入口统一并入单页工作台：带着阶段参数跳转，状态不会丢。 */
export default function WorkbenchRedirect({ stage }: { stage: StageId }) {
  const router = useRouter();

  useEffect(() => {
    void router.replace(`/?stage=${stage}`);
  }, [router, stage]);

  return (
    <div className="h-screen flex items-center justify-center text-xs text-gray-500">
      <Loader2 className="w-4 h-4 animate-spin mr-2" /> 正在进入工作台…
    </div>
  );
}
