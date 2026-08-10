import React from "react";
import { useTranslation } from "react-i18next";
import type { AutoSubmitKey, ClipboardHandling } from "@/bindings";
import { RecordingRetentionPeriod } from "@/bindings";
import { ShowOverlay } from "../ShowOverlay";
import { ModelUnloadTimeoutSetting } from "../ModelUnloadTimeout";
import { CustomWords } from "../CustomWords";
import { PasteMethodSetting } from "../PasteMethod";
import { TypingToolSetting } from "../TypingTool";
import { KeyboardImplementationSelector } from "../debug/KeyboardImplementationSelector";
import { AccelerationSelector } from "../AccelerationSelector";
import { Dropdown } from "../../ui/Dropdown";
import { Input } from "../../ui/Input";
import { SettingContainer } from "../../ui/SettingContainer";
import { SettingsGroup } from "../../ui/SettingsGroup";
import { ToggleSwitch } from "../../ui/ToggleSwitch";
import { useSettings } from "../../../hooks/useSettings";

export const AdvancedSettings: React.FC = () => {
  const { t } = useTranslation();
  const { getSetting } = useSettings();
  const experimentalEnabled = getSetting("experimental_enabled") || false;

  return (
    <div className="max-w-3xl w-full mx-auto space-y-6">
      <SettingsGroup title={t("settings.advanced.groups.app")}>
        <StartHidden descriptionMode="tooltip" grouped={true} />
        <AutostartToggle descriptionMode="tooltip" grouped={true} />
        <ShowTrayIcon descriptionMode="tooltip" grouped={true} />
        <ShowOverlay descriptionMode="tooltip" grouped={true} />
        <ModelUnloadTimeoutSetting descriptionMode="tooltip" grouped={true} />
        <ExperimentalToggle descriptionMode="tooltip" grouped={true} />
      </SettingsGroup>

      <SettingsGroup title={t("settings.advanced.groups.output")}>
        <PasteMethodSetting descriptionMode="tooltip" grouped={true} />
        <TypingToolSetting descriptionMode="tooltip" grouped={true} />
        <ClipboardHandlingSetting descriptionMode="tooltip" grouped={true} />
        <AutoSubmit descriptionMode="tooltip" grouped={true} />
        <HerdrBinding descriptionMode="tooltip" grouped={true} />
      </SettingsGroup>

      <SettingsGroup title={t("settings.advanced.groups.transcription")}>
        <VoiceActivityDetection descriptionMode="tooltip" grouped={true} />
        <CustomWords descriptionMode="tooltip" grouped />
        <AppendTrailingSpace descriptionMode="tooltip" grouped={true} />
      </SettingsGroup>

      <SettingsGroup title={t("settings.advanced.groups.history")}>
        <HistoryLimit descriptionMode="tooltip" grouped={true} />
        <RecordingRetentionPeriodSelector
          descriptionMode="tooltip"
          grouped={true}
        />
      </SettingsGroup>

      {experimentalEnabled && (
        <SettingsGroup title={t("settings.advanced.groups.experimental")}>
          <PostProcessingToggle descriptionMode="tooltip" grouped={true} />
          <KeyboardImplementationSelector
            descriptionMode="tooltip"
            grouped={true}
          />
          <AccelerationSelector descriptionMode="tooltip" grouped={true} />
          <LazyStreamClose descriptionMode="tooltip" grouped={true} />
        </SettingsGroup>
      )}
    </div>
  );
};

interface StartHiddenProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const StartHidden: React.FC<StartHiddenProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const startHidden = getSetting("start_hidden") ?? false;

    return (
      <ToggleSwitch
        checked={startHidden}
        onChange={(enabled) => updateSetting("start_hidden", enabled)}
        isUpdating={isUpdating("start_hidden")}
        label={t("settings.advanced.startHidden.label")}
        description={t("settings.advanced.startHidden.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
        tooltipPosition="bottom"
      />
    );
  },
);

interface AutostartToggleProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const AutostartToggle: React.FC<AutostartToggleProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const autostartEnabled = getSetting("autostart_enabled") ?? false;

    return (
      <ToggleSwitch
        checked={autostartEnabled}
        onChange={(enabled) => updateSetting("autostart_enabled", enabled)}
        isUpdating={isUpdating("autostart_enabled")}
        label={t("settings.advanced.autostart.label")}
        description={t("settings.advanced.autostart.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);

interface ShowTrayIconProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const ShowTrayIcon: React.FC<ShowTrayIconProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const showTrayIcon = getSetting("show_tray_icon") ?? true;

    return (
      <ToggleSwitch
        checked={showTrayIcon}
        onChange={(enabled) => updateSetting("show_tray_icon", enabled)}
        isUpdating={isUpdating("show_tray_icon")}
        label={t("settings.advanced.showTrayIcon.label")}
        description={t("settings.advanced.showTrayIcon.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
        tooltipPosition="bottom"
      />
    );
  },
);

interface ExperimentalToggleProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const ExperimentalToggle: React.FC<ExperimentalToggleProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const enabled = getSetting("experimental_enabled") || false;

    return (
      <ToggleSwitch
        checked={enabled}
        onChange={(enabled) => updateSetting("experimental_enabled", enabled)}
        isUpdating={isUpdating("experimental_enabled")}
        label={t("settings.advanced.experimentalToggle.label")}
        description={t("settings.advanced.experimentalToggle.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);

interface ClipboardHandlingProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const ClipboardHandlingSetting: React.FC<ClipboardHandlingProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const clipboardHandlingOptions = [
      {
        value: "dont_modify",
        label: t("settings.advanced.clipboardHandling.options.dontModify"),
      },
      {
        value: "copy_to_clipboard",
        label: t("settings.advanced.clipboardHandling.options.copyToClipboard"),
      },
    ];

    const selectedHandling = (getSetting("clipboard_handling") ||
      "dont_modify") as ClipboardHandling;

    return (
      <SettingContainer
        title={t("settings.advanced.clipboardHandling.title")}
        description={t("settings.advanced.clipboardHandling.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      >
        <Dropdown
          options={clipboardHandlingOptions}
          selectedValue={selectedHandling}
          onSelect={(value) =>
            updateSetting("clipboard_handling", value as ClipboardHandling)
          }
          disabled={isUpdating("clipboard_handling")}
        />
      </SettingContainer>
    );
  },
);

interface AutoSubmitProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

type AutoSubmitOptionValue = AutoSubmitKey | "off";

const AutoSubmit: React.FC<AutoSubmitProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const enabled = getSetting("auto_submit") ?? false;
    const selectedKey = (getSetting("auto_submit_key") ||
      "enter") as AutoSubmitKey;
    const selectedValue: AutoSubmitOptionValue = enabled ? selectedKey : "off";
    const submitWithMetaLabel = t(
      "settings.advanced.autoSubmit.options.superEnter",
    );

    const autoSubmitOptions = [
      {
        value: "off",
        label: t("settings.advanced.autoSubmit.options.off"),
      },
      {
        value: "enter",
        label: t("settings.advanced.autoSubmit.options.enter"),
      },
      {
        value: "ctrl_enter",
        label: t("settings.advanced.autoSubmit.options.ctrlEnter"),
      },
      {
        value: "cmd_enter",
        label: submitWithMetaLabel,
      },
    ];

    const handleAutoSubmitSelect = async (value: string) => {
      const selected = value as AutoSubmitOptionValue;

      if (selected === "off") {
        await updateSetting("auto_submit", false);
        return;
      }

      await updateSetting("auto_submit_key", selected as AutoSubmitKey);
      if (!enabled) {
        await updateSetting("auto_submit", true);
      }
    };

    return (
      <SettingContainer
        title={t("settings.advanced.autoSubmit.title")}
        description={t("settings.advanced.autoSubmit.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      >
        <Dropdown
          options={autoSubmitOptions}
          selectedValue={selectedValue}
          onSelect={handleAutoSubmitSelect}
          disabled={isUpdating("auto_submit") || isUpdating("auto_submit_key")}
        />
      </SettingContainer>
    );
  },
);

interface HerdrBindingProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const HerdrBinding: React.FC<HerdrBindingProps> = React.memo(
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

interface PostProcessingToggleProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const PostProcessingToggle: React.FC<PostProcessingToggleProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const enabled = getSetting("post_process_enabled") || false;

    return (
      <ToggleSwitch
        checked={enabled}
        onChange={(enabled) => updateSetting("post_process_enabled", enabled)}
        isUpdating={isUpdating("post_process_enabled")}
        label={t("settings.debug.postProcessingToggle.label")}
        description={t("settings.debug.postProcessingToggle.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);

interface AppendTrailingSpaceProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const AppendTrailingSpace: React.FC<AppendTrailingSpaceProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const enabled = getSetting("append_trailing_space") ?? false;

    return (
      <ToggleSwitch
        checked={enabled}
        onChange={(enabled) => updateSetting("append_trailing_space", enabled)}
        isUpdating={isUpdating("append_trailing_space")}
        label={t("settings.debug.appendTrailingSpace.label")}
        description={t("settings.debug.appendTrailingSpace.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);

interface HistoryLimitProps {
  descriptionMode?: "tooltip" | "inline";
  grouped?: boolean;
}

const HistoryLimit: React.FC<HistoryLimitProps> = ({
  descriptionMode = "inline",
  grouped = false,
}) => {
  const { t } = useTranslation();
  const { getSetting, updateSetting, isUpdating } = useSettings();

  const historyLimit = getSetting("history_limit") ?? 5;

  const handleChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseInt(event.target.value, 10);
    if (!isNaN(value) && value >= 0) {
      updateSetting("history_limit", value);
    }
  };

  return (
    <SettingContainer
      title={t("settings.debug.historyLimit.title")}
      description={t("settings.debug.historyLimit.description")}
      descriptionMode={descriptionMode}
      grouped={grouped}
      layout="horizontal"
    >
      <div className="flex items-center space-x-2">
        <Input
          type="number"
          min="0"
          max="1000"
          value={historyLimit}
          onChange={handleChange}
          disabled={isUpdating("history_limit")}
          className="w-20"
        />
        <span className="text-sm text-text">
          {t("settings.debug.historyLimit.entries")}
        </span>
      </div>
    </SettingContainer>
  );
};

interface RecordingRetentionPeriodProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const RecordingRetentionPeriodSelector: React.FC<RecordingRetentionPeriodProps> =
  React.memo(({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const selectedRetentionPeriod =
      getSetting("recording_retention_period") || "never";
    const historyLimit = getSetting("history_limit") || 5;

    const handleRetentionPeriodSelect = async (period: string) => {
      await updateSetting(
        "recording_retention_period",
        period as RecordingRetentionPeriod,
      );
    };

    const retentionOptions = [
      { value: "never", label: t("settings.debug.recordingRetention.never") },
      {
        value: "preserve_limit",
        label: t("settings.debug.recordingRetention.preserveLimit", {
          count: Number(historyLimit),
        }),
      },
      { value: "days3", label: t("settings.debug.recordingRetention.days3") },
      { value: "weeks2", label: t("settings.debug.recordingRetention.weeks2") },
      {
        value: "months3",
        label: t("settings.debug.recordingRetention.months3"),
      },
    ];

    return (
      <SettingContainer
        title={t("settings.debug.recordingRetention.title")}
        description={t("settings.debug.recordingRetention.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      >
        <Dropdown
          options={retentionOptions}
          selectedValue={selectedRetentionPeriod}
          onSelect={handleRetentionPeriodSelect}
          placeholder={t("settings.debug.recordingRetention.placeholder")}
          disabled={isUpdating("recording_retention_period")}
        />
      </SettingContainer>
    );
  });

RecordingRetentionPeriodSelector.displayName =
  "RecordingRetentionPeriodSelector";

interface VoiceActivityDetectionProps {
  descriptionMode?: "tooltip" | "inline";
  grouped?: boolean;
}

const VoiceActivityDetection: React.FC<VoiceActivityDetectionProps> = ({
  descriptionMode = "tooltip",
  grouped = false,
}) => {
  const { t } = useTranslation();
  const { getSetting, updateSetting, isUpdating } = useSettings();
  const enabled = getSetting("vad_enabled") ?? true;

  return (
    <ToggleSwitch
      checked={enabled}
      onChange={(enabled) => updateSetting("vad_enabled", enabled)}
      isUpdating={isUpdating("vad_enabled")}
      label={t("settings.advanced.voiceActivityDetection.title")}
      description={t("settings.advanced.voiceActivityDetection.description")}
      descriptionMode={descriptionMode}
      grouped={grouped}
    />
  );
};

interface LazyStreamCloseProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const LazyStreamClose: React.FC<LazyStreamCloseProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const enabled = getSetting("lazy_stream_close") ?? false;

    return (
      <ToggleSwitch
        checked={enabled}
        onChange={(enabled) => updateSetting("lazy_stream_close", enabled)}
        isUpdating={isUpdating("lazy_stream_close")}
        label={t("settings.advanced.lazyStreamClose.label")}
        description={t("settings.advanced.lazyStreamClose.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);
