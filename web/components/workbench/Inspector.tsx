import { Layers, Package, SlidersHorizontal, Sparkles } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useWorkbench } from '../../lib/workbench-context';
import { INSPECTOR_TABS, type InspectorTab } from '../../lib/workbench';
import PromptPanel from './panels/PromptPanel';
import LayersPanel from './panels/LayersPanel';
import ParamsPanel from './panels/ParamsPanel';
import ExportPanel from './panels/ExportPanel';

const TAB_ICONS: Record<InspectorTab, LucideIcon> = {
  prompt: Sparkles,
  layers: Layers,
  params: SlidersHorizontal,
  export: Package,
};

export default function Inspector() {
  const { inspector, setInspector, layers } = useWorkbench();

  return (
    <aside className="panel-inspector flex w-[348px] shrink-0 flex-col min-h-0 2xl:w-[372px]">
      <div className="shrink-0 border-b border-black/[0.07] px-3 pb-2.5 pt-3">
        <div className="mb-2 flex items-center justify-between px-0.5">
          <span className="text-[10px] font-medium tracking-[0.2em] text-haze-600">INSPECTOR</span>
          <span className="text-[10px] text-haze-500">操作台</span>
        </div>
        <div
          role="tablist"
          aria-label="操作台面板"
          className="flex gap-1 rounded-xl border border-black/[0.07] bg-ink-950/50 p-1"
        >
          {INSPECTOR_TABS.map((tab) => {
            const Icon = TAB_ICONS[tab.id];
            const active = inspector === tab.id;
            return (
              <button
                key={tab.id}
                role="tab"
                aria-selected={active}
                onClick={() => setInspector(tab.id)}
                className={`flex flex-1 items-center justify-center gap-1 rounded-lg px-1 py-1.5 text-[11px] font-medium transition-colors ${
                  active
                    ? 'bg-white text-haze-100 shadow-card'
                    : 'text-haze-500 hover:bg-black/[0.03] hover:text-haze-300'
                }`}
              >
                <Icon className={`h-3.5 w-3.5 ${active ? 'text-sakura-600' : 'text-haze-600'}`} />
                {tab.label}
                {tab.id === 'layers' && layers.length > 0 && (
                  <span className="text-[9px] tabular-nums text-haze-600">{layers.length}</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3.5">
        {inspector === 'prompt' && <PromptPanel />}
        {inspector === 'layers' && <LayersPanel />}
        {inspector === 'params' && <ParamsPanel />}
        {inspector === 'export' && <ExportPanel />}
      </div>
    </aside>
  );
}
