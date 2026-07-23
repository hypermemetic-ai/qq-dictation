import React from "react";
import { useTranslation } from "react-i18next";
import { ToggleSwitch } from "../ui/ToggleSwitch";
import { useSettings } from "../../hooks/useSettings";

interface HerdrBindingProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

export const HerdrBinding: React.FC<HerdrBindingProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const enabled = getSetting("herdr_binding_enabled") ?? true;

    return (
      <ToggleSwitch
        checked={enabled}
        onChange={(enabled) => updateSetting("herdr_binding_enabled", enabled)}
        isUpdating={isUpdating("herdr_binding_enabled")}
        label={t("settings.advanced.herdrBinding.label")}
        description={t("settings.advanced.herdrBinding.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);
