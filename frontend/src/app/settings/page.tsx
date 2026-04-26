"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  SettingsNav,
  type SettingsTab,
} from "./_components/SettingsNav";
import { GeneralTab } from "./_components/GeneralTab";
import { ModelsTab } from "./_components/ModelsTab";
import { ParamsTab } from "./_components/ParamsTab";
import { NotificationsTab } from "./_components/NotificationsTab";
import { ShortcutsTab } from "./_components/ShortcutsTab";

export default function SettingsPage() {
  const [tab, setTab] = useState<SettingsTab>("general");

  return (
    <div className="px-8 py-6">
      <PageHeader title="설정" description="프로젝트, 연결, 기본값, 알림" />

      <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside>
          <SettingsNav value={tab} onChange={setTab} />
        </aside>
        <section>
          <AnimatePresence mode="wait">
            <motion.div
              key={tab}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
            >
              {tab === "general" && <GeneralTab />}
              {tab === "models" && <ModelsTab />}
              {tab === "params" && <ParamsTab />}
              {tab === "notifications" && <NotificationsTab />}
              {tab === "shortcuts" && <ShortcutsTab />}
            </motion.div>
          </AnimatePresence>
        </section>
      </div>
    </div>
  );
}
