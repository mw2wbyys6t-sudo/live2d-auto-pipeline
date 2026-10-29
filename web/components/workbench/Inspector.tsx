import { useWorkbench } from '../../lib/workbench-context';
import { INSPECTOR_TABS } from '../../lib/workbench';
import PromptPanel from './panels/PromptPanel';
import LayersPanel from './panels/LayersPanel';
import ParamsPanel from './panels/ParamsPanel';
import ExportPanel from './panels/ExportPanel';

export default function Inspector() {
  const { inspector, setInspector, layers } = useWorkbench();

  return (
    <aside className="w-[360px] shrink-0 border-l border-gray-800 bg-[#12121a] flex flex-col min-h-0">
      <div className="flex items-center gap-1 px-3 pt-3 border-b border-gray-800">
        {INSPECTOR_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setInspector(tab.id)}
            className={`flex-1 px-2 py-2 rounded-t-lg text-xs font-medium transition-colors ${
              inspector === tab.id
                ? 'bg-[#1a1a24] text-white border border-gray-800 border-b-transparent -mb-px'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {tab.label}
            {tab.id === 'layers' && layers.length > 0 && (
              <span className="ml-1 text-[10px] text-gray-600">{layers.length}</span>
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4 min-h-0">
        {inspector === 'prompt' && <PromptPanel />}
        {inspector === 'layers' && <LayersPanel />}
        {inspector === 'params' && <ParamsPanel />}
        {inspector === 'export' && <ExportPanel />}
      </div>
    </aside>
  );
}
