import React from "react";
import { useTranslation } from "react-i18next";
import { MicrophoneSelector } from "../MicrophoneSelector";
import { ShortcutInput } from "../ShortcutInput";
import { OutputDeviceSelector } from "../OutputDeviceSelector";
import { ModelSettingsCard } from "./ModelSettingsCard";
import { SettingsGroup } from "../../ui/SettingsGroup";
import { Slider } from "../../ui/Slider";
import { ToggleSwitch } from "../../ui/ToggleSwitch";
import { useSettings } from "../../../hooks/useSettings";

export const GeneralSettings: React.FC = () => {
  const { t } = useTranslation();
  const { audioFeedbackEnabled } = useSettings();
  return (
    <div className="max-w-3xl w-full mx-auto space-y-6">
      <SettingsGroup title={t("settings.general.title")}>
        <ShortcutInput shortcutId="transcribe" grouped={true} />
        <PushToTalk descriptionMode="tooltip" grouped={true} />
        {/* Dynamic cancel shortcuts are not used on Linux. */}
      </SettingsGroup>
      <ModelSettingsCard />
      <SettingsGroup title={t("settings.sound.title")}>
        <MicrophoneSelector descriptionMode="tooltip" grouped={true} />
        <MuteWhileRecording descriptionMode="tooltip" grouped={true} />
        <AudioFeedback descriptionMode="tooltip" grouped={true} />
        <OutputDeviceSelector
          descriptionMode="tooltip"
          grouped={true}
          disabled={!audioFeedbackEnabled}
        />
        <VolumeSlider disabled={!audioFeedbackEnabled} />
      </SettingsGroup>
    </div>
  );
};

interface PushToTalkProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const PushToTalk: React.FC<PushToTalkProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const pttEnabled = getSetting("push_to_talk") || false;

    return (
      <ToggleSwitch
        checked={pttEnabled}
        onChange={(enabled) => updateSetting("push_to_talk", enabled)}
        isUpdating={isUpdating("push_to_talk")}
        label={t("settings.general.pushToTalk.label")}
        description={t("settings.general.pushToTalk.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);

interface AudioFeedbackProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const AudioFeedback: React.FC<AudioFeedbackProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();
    const audioFeedbackEnabled = getSetting("audio_feedback") || false;

    return (
      <div className="flex flex-col">
        <ToggleSwitch
          checked={audioFeedbackEnabled}
          onChange={(enabled) => updateSetting("audio_feedback", enabled)}
          isUpdating={isUpdating("audio_feedback")}
          label={t("settings.sound.audioFeedback.label")}
          description={t("settings.sound.audioFeedback.description")}
          descriptionMode={descriptionMode}
          grouped={grouped}
        />
      </div>
    );
  },
);

interface MuteWhileRecordingToggleProps {
  descriptionMode?: "inline" | "tooltip";
  grouped?: boolean;
}

const MuteWhileRecording: React.FC<MuteWhileRecordingToggleProps> = React.memo(
  ({ descriptionMode = "tooltip", grouped = false }) => {
    const { t } = useTranslation();
    const { getSetting, updateSetting, isUpdating } = useSettings();

    const muteEnabled = getSetting("mute_while_recording") ?? false;

    return (
      <ToggleSwitch
        checked={muteEnabled}
        onChange={(enabled) => updateSetting("mute_while_recording", enabled)}
        isUpdating={isUpdating("mute_while_recording")}
        label={t("settings.debug.muteWhileRecording.label")}
        description={t("settings.debug.muteWhileRecording.description")}
        descriptionMode={descriptionMode}
        grouped={grouped}
      />
    );
  },
);

const VolumeSlider: React.FC<{ disabled?: boolean }> = ({
  disabled = false,
}) => {
  const { t } = useTranslation();
  const { getSetting, updateSetting } = useSettings();
  const audioFeedbackVolume = getSetting("audio_feedback_volume") ?? 0.5;

  return (
    <Slider
      value={audioFeedbackVolume}
      onChange={(value: number) =>
        updateSetting("audio_feedback_volume", value)
      }
      min={0}
      max={1}
      label={t("settings.sound.volume.title")}
      description={t("settings.sound.volume.description")}
      descriptionMode="tooltip"
      grouped
      formatValue={(value) => `${Math.round(value * 100)}%`}
      disabled={disabled}
    />
  );
};
