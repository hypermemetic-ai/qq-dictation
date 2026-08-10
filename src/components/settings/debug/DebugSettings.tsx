import React from "react";
import { useTranslation } from "react-i18next";
import type { LogLevel } from "../../../bindings";
import { LiveLogViewer } from "./LiveLogViewer";
import { SoundPicker } from "../SoundPicker";
import { Dropdown, type DropdownOption } from "../../ui/Dropdown";
import { SettingContainer } from "../../ui/SettingContainer";
import { SettingsGroup } from "../../ui/SettingsGroup";
import { Slider } from "../../ui/Slider";
import { ToggleSwitch } from "../../ui/ToggleSwitch";
import { useSettings } from "../../../hooks/useSettings";

export const DebugSettings: React.FC = () => {
  const { t } = useTranslation();

  return (
    <div className="max-w-3xl w-full mx-auto space-y-6">
      <SettingsGroup title={t("settings.debug.title")}>
        <LogLevelSelector grouped={true} />
        <SoundPicker
          label={t("settings.debug.soundTheme.label")}
          description={t("settings.debug.soundTheme.description")}
        />
        <WordCorrectionThreshold descriptionMode="tooltip" grouped={true} />
        <PasteDelay descriptionMode="tooltip" grouped={true} />
        <PasteDelay
          descriptionMode="tooltip"
          grouped={true}
          settingKey="paste_delay_after_ms"
          labelKey="settings.debug.pasteDelayAfter.title"
          descriptionKey="settings.debug.pasteDelayAfter.description"
        />
        <RecordingBuffer descriptionMode="tooltip" grouped={true} />
        <AlwaysOnMicrophone descriptionMode="tooltip" grouped={true} />
        <LiveLogViewer descriptionMode="tooltip" grouped={true} />
      </SettingsGroup>
    </div>
  );
};

const LOG_LEVEL_OPTIONS: DropdownOption[] = [
  { value: "error", label: "Error" },
  { value: "warn", label: "Warn" },
  { value: "info", label: "Info" },
  { value: "debug", label: "Debug" },
  { value: "trace", label: "Trace" },
];

interface LogLevelSelectorProps {
  descriptionMode?: "tooltip" | "inline";
  grouped?: boolean;
}

const LogLevelSelector: React.FC<LogLevelSelectorProps> = ({
  descriptionMode = "tooltip",
  grouped = false,
}) => {
  const { t } = useTranslation();
  const { settings, updateSetting, isUpdating } = useSettings();
  const currentLevel = settings?.log_level ?? "debug";

  const handleSelect = async (value: string) => {
    if (value === currentLevel) return;

    try {
      await updateSetting("log_level", value as LogLevel);
    } catch (error) {
      console.error("Failed to update log level:", error);
    }
  };

  return (
    <SettingContainer
      title={t("settings.debug.logLevel.title")}
      description={t("settings.debug.logLevel.description")}
      descriptionMode={descriptionMode}
      grouped={grouped}
      layout="horizontal"
    >
      <Dropdown
        options={LOG_LEVEL_OPTIONS}
        selectedValue={currentLevel}
        onSelect={handleSelect}
        disabled={!settings || isUpdating("log_level")}
      />
    </SettingContainer>
  );
};

interface WordCorrectionThresholdProps {
  descriptionMode?: "tooltip" | "inline";
  grouped?: boolean;
}

const WordCorrectionThreshold: React.FC<WordCorrectionThresholdProps> = ({
  descriptionMode = "tooltip",
  grouped = false,
}) => {
  const { t } = useTranslation();
  const { settings, updateSetting } = useSettings();

  const handleThresholdChange = (value: number) => {
    updateSetting("word_correction_threshold", value);
  };

  return (
    <Slider
      value={settings?.word_correction_threshold ?? 0.18}
      onChange={handleThresholdChange}
      min={0.0}
      max={1.0}
      label={t("settings.debug.wordCorrectionThreshold.title")}
      description={t("settings.debug.wordCorrectionThreshold.description")}
      descriptionMode={descriptionMode}
      grouped={grouped}
    />
  );
};

type PasteDelayKey = "paste_delay_ms" | "paste_delay_after_ms";

interface PasteDelayProps {
  descriptionMode?: "tooltip" | "inline";
  grouped?: boolean;
  settingKey?: PasteDelayKey;
  labelKey?: string;
  descriptionKey?: string;
}

const PasteDelay: React.FC<PasteDelayProps> = ({
  descriptionMode = "tooltip",
  grouped = false,
  settingKey = "paste_delay_ms",
  labelKey = "settings.debug.pasteDelay.title",
  descriptionKey = "settings.debug.pasteDelay.description",
}) => {
  const { t } = useTranslation();
  const { settings, updateSetting } = useSettings();

  const handleDelayChange = (value: number) => {
    updateSetting(settingKey, value);
  };

  return (
    <Slider
      value={settings?.[settingKey] ?? 60}
      onChange={handleDelayChange}
      min={10}
      max={500}
      step={10}
      label={t(labelKey)}
      description={t(descriptionKey)}
      descriptionMode={descriptionMode}
      grouped={grouped}
      formatValue={(v) => `${v}ms`}
    />
  );
};

interface RecordingBufferProps {
  descriptionMode?: "tooltip" | "inline";
  grouped?: boolean;
}

const RecordingBuffer: React.FC<RecordingBufferProps> = ({
  descriptionMode = "tooltip",
  grouped = false,
}) => {
  const { t } = useTranslation();
  const { settings, updateSetting } = useSettings();

  const handleBufferChange = (value: number) => {
    updateSetting("extra_recording_buffer_ms", value);
  };

  return (
    <Slider
      value={settings?.extra_recording_buffer_ms ?? 0}
      onChange={handleBufferChange}
      min={0}
      max={1500}
      step={50}
      label={t("settings.debug.recordingBuffer.title")}
      description={t("settings.debug.recordingBuffer.description")}
      descriptionMode={descriptionMode}
      grouped={grouped}
      formatValue={(v) => `${v}ms`}
    />
  );
};

interface AlwaysOnMicrophoneProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const AlwaysOnMicrophone: React.FC<AlwaysOnMicrophoneProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const alwaysOnMode = getSetting("always_on_microphone") || false;

    return (
      <ToggleSwitch
        checked={alwaysOnMode}
        onChange={(enabled) => updateSetting("always_on_microphone", enabled)}
        isUpdating={isUpdating("always_on_microphone")}
        label={t("settings.debug.alwaysOnMicrophone.label")}
        description={t("settings.debug.alwaysOnMicrophone.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);
