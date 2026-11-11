import type { ReactNode } from 'react';

interface StageHeaderProps {
  title: string;
  description: ReactNode;
  /** 标题左侧的小图标底座 */
  icon?: ReactNode;
  /** 该阶段的主行动按钮 / 工具组 */
  actions?: ReactNode;
  /** 标题右侧的状态标签 */
  badge?: ReactNode;
}

/** 四个阶段页共用的画布页眉：左标题 + 一句解释，右侧放本阶段动作。 */
export default function StageHeader({ title, description, icon, actions, badge }: StageHeaderProps) {
  return (
    <header className="flex shrink-0 items-start justify-between gap-4">
      <div className="flex min-w-0 items-center gap-2.5">
        {icon && (
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] border border-black/[0.08] bg-black/[0.03] text-sakura-600">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-sm font-semibold tracking-wide text-haze-100">{title}</h2>
            {badge}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-haze-500">{description}</p>
        </div>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">{actions}</div>}
    </header>
  );
}
