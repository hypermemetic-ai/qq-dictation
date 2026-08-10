import React, { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCcw } from "lucide-react";
import type { PostProcessProvider } from "@/bindings";
import { useSettings } from "../../../hooks/useSettings";
import { Dropdown, type DropdownOption } from "../../ui/Dropdown";
import { Input } from "../../ui/Input";
import { ResetButton } from "../../ui/ResetButton";
import { Select } from "../../ui/Select";
import { SettingContainer } from "../../ui/SettingContainer";
type ModelOption = {
  value: string;
  label: string;
};
interface ApiKeyFieldProps {
  value: string;
  onBlur: (value: string) => void;
  disabled: boolean;
  placeholder?: string;
  className?: string;
}

const ApiKeyField: React.FC<ApiKeyFieldProps> = React.memo(
  ({ value, onBlur, disabled, placeholder, className = "" }) => {
    const [localValue, setLocalValue] = useState(value);

    // Sync with prop changes
    React.useEffect(() => {
      setLocalValue(value);
    }, [value]);

    return (
      <Input
        type="password"
        value={localValue}
        onChange={(event) => setLocalValue(event.target.value)}
        onBlur={() => onBlur(localValue)}
        placeholder={placeholder}
        variant="compact"
        disabled={disabled}
        className={`flex-1 min-w-[320px] ${className}`}
      />
    );
  },
);

ApiKeyField.displayName = "ApiKeyField";
interface BaseUrlFieldProps {
  value: string;
  onBlur: (value: string) => void;
  disabled: boolean;
  placeholder?: string;
  className?: string;
}

const BaseUrlField: React.FC<BaseUrlFieldProps> = React.memo(
  ({ value, onBlur, disabled, placeholder, className = "" }) => {
    const [localValue, setLocalValue] = useState(value);

    // Sync with prop changes
    React.useEffect(() => {
      setLocalValue(value);
    }, [value]);

    const disabledMessage = disabled
      ? "Base URL is managed by the selected provider."
      : undefined;

    return (
      <Input
        type="text"
        value={localValue}
        onChange={(event) => setLocalValue(event.target.value)}
        onBlur={() => onBlur(localValue)}
        placeholder={placeholder}
        variant="compact"
        disabled={disabled}
        className={`flex-1 min-w-[360px] ${className}`}
        title={disabledMessage}
      />
    );
  },
);

BaseUrlField.displayName = "BaseUrlField";
type ModelSelectProps = {
  value: string;
  options: ModelOption[];
  disabled?: boolean;
  placeholder?: string;
  isLoading?: boolean;
  onSelect: (value: string) => void;
  onCreate: (value: string) => void;
  onBlur: () => void;
  className?: string;
};

const ModelSelect: React.FC<ModelSelectProps> = React.memo(
  ({
    value,
    options,
    disabled,
    placeholder,
    isLoading,
    onSelect,
    onCreate,
    onBlur,
    className = "flex-1 min-w-[360px]",
  }) => {
    const handleCreate = (inputValue: string) => {
      const trimmed = inputValue.trim();
      if (!trimmed) return;
      onCreate(trimmed);
    };

    const computedClassName = `text-sm ${className}`;

    return (
      <Select
        className={computedClassName}
        value={value || null}
        options={options}
        onChange={(selected) => onSelect(selected ?? "")}
        onCreateOption={handleCreate}
        onBlur={onBlur}
        placeholder={placeholder}
        disabled={disabled}
        isLoading={isLoading}
        isCreatable
        formatCreateLabel={(input) => `Use "${input}"`}
      />
    );
  },
);

ModelSelect.displayName = "ModelSelect";
interface ProviderSelectProps {
  options: DropdownOption[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

const ProviderSelect: React.FC<ProviderSelectProps> = React.memo(
  ({ options, value, onChange, disabled }) => {
    return (
      <Dropdown
        options={options}
        selectedValue={value}
        onSelect={onChange}
        disabled={disabled}
        className="flex-1"
      />
    );
  },
);

ProviderSelect.displayName = "ProviderSelect";
type PostProcessProviderState = {
  providerOptions: DropdownOption[];
  selectedProviderId: string;
  selectedProvider: PostProcessProvider | undefined;
  isCustomProvider: boolean;
  baseUrl: string;
  handleBaseUrlChange: (value: string) => void;
  isBaseUrlUpdating: boolean;
  apiKey: string;
  handleApiKeyChange: (value: string) => void;
  isApiKeyUpdating: boolean;
  model: string;
  handleModelChange: (value: string) => void;
  modelOptions: ModelOption[];
  isModelUpdating: boolean;
  isFetchingModels: boolean;
  handleProviderSelect: (providerId: string) => void;
  handleModelSelect: (value: string) => void;
  handleModelCreate: (value: string) => void;
  handleRefreshModels: () => void;
};

const usePostProcessProviderState = (): PostProcessProviderState => {
  const {
    settings,
    isUpdating,
    setPostProcessProvider,
    updatePostProcessBaseUrl,
    updatePostProcessApiKey,
    updatePostProcessModel,
    fetchPostProcessModels,
    postProcessModelOptions,
  } = useSettings();

  // Settings are guaranteed to have providers after migration
  const providers = settings?.post_process_providers || [];

  const selectedProviderId = useMemo(() => {
    return settings?.post_process_provider_id || providers[0]?.id || "openai";
  }, [providers, settings?.post_process_provider_id]);

  const selectedProvider = useMemo(() => {
    return (
      providers.find((provider) => provider.id === selectedProviderId) ||
      providers[0]
    );
  }, [providers, selectedProviderId]);

  // Use settings directly as single source of truth
  const baseUrl = selectedProvider?.base_url ?? "";
  const apiKey = settings?.post_process_api_keys?.[selectedProviderId] ?? "";
  const model = settings?.post_process_models?.[selectedProviderId] ?? "";

  const providerOptions = useMemo<DropdownOption[]>(() => {
    return providers.map((provider) => ({
      value: provider.id,
      label: provider.label,
    }));
  }, [providers]);

  const handleProviderSelect = useCallback(
    async (providerId: string) => {
      if (providerId === selectedProviderId) return;

      await setPostProcessProvider(providerId);

      // Auto-fetch available models for a configured provider so the model
      // dropdown reflects the endpoint's current values.
      const provider = providers.find(
        (candidate) => candidate.id === providerId,
      );
      const apiKey = settings?.post_process_api_keys?.[providerId] ?? "";
      const hasBaseUrl = (provider?.base_url ?? "").trim() !== "";
      const hasApiKey = apiKey.trim() !== "";

      if (provider?.id === "custom" ? hasBaseUrl : hasApiKey) {
        void fetchPostProcessModels(providerId);
      }
    },
    [
      selectedProviderId,
      setPostProcessProvider,
      fetchPostProcessModels,
      providers,
      settings,
    ],
  );

  const handleBaseUrlChange = useCallback(
    (value: string) => {
      if (!selectedProvider || selectedProvider.id !== "custom") {
        return;
      }
      const trimmed = value.trim();
      if (trimmed && trimmed !== baseUrl) {
        void updatePostProcessBaseUrl(selectedProvider.id, trimmed);
      }
    },
    [selectedProvider, baseUrl, updatePostProcessBaseUrl],
  );

  const handleApiKeyChange = useCallback(
    (value: string) => {
      const trimmed = value.trim();
      if (trimmed !== apiKey) {
        void updatePostProcessApiKey(selectedProviderId, trimmed);
      }
    },
    [apiKey, selectedProviderId, updatePostProcessApiKey],
  );

  const handleModelChange = useCallback(
    (value: string) => {
      const trimmed = value.trim();
      if (trimmed !== model) {
        void updatePostProcessModel(selectedProviderId, trimmed);
      }
    },
    [model, selectedProviderId, updatePostProcessModel],
  );

  const handleModelSelect = useCallback(
    (value: string) => {
      void updatePostProcessModel(selectedProviderId, value.trim());
    },
    [selectedProviderId, updatePostProcessModel],
  );

  const handleModelCreate = useCallback(
    (value: string) => {
      void updatePostProcessModel(selectedProviderId, value);
    },
    [selectedProviderId, updatePostProcessModel],
  );

  const handleRefreshModels = useCallback(() => {
    void fetchPostProcessModels(selectedProviderId);
  }, [fetchPostProcessModels, selectedProviderId]);

  const availableModelsRaw = postProcessModelOptions[selectedProviderId] || [];

  const modelOptions = useMemo<ModelOption[]>(() => {
    const seen = new Set<string>();
    const options: ModelOption[] = [];

    const upsert = (value: string | null | undefined) => {
      const trimmed = value?.trim();
      if (!trimmed || seen.has(trimmed)) return;
      seen.add(trimmed);
      options.push({ value: trimmed, label: trimmed });
    };

    // Add available models from API
    for (const candidate of availableModelsRaw) {
      upsert(candidate);
    }

    // Ensure current model is in the list
    upsert(model);

    return options;
  }, [availableModelsRaw, model]);

  const isBaseUrlUpdating = isUpdating(
    `post_process_base_url:${selectedProviderId}`,
  );
  const isApiKeyUpdating = isUpdating(
    `post_process_api_key:${selectedProviderId}`,
  );
  const isModelUpdating = isUpdating(
    `post_process_model:${selectedProviderId}`,
  );
  const isFetchingModels = isUpdating(
    `post_process_models_fetch:${selectedProviderId}`,
  );

  const isCustomProvider = selectedProvider?.id === "custom";

  // No automatic fetching - user must click refresh button

  return {
    providerOptions,
    selectedProviderId,
    selectedProvider,
    isCustomProvider,
    baseUrl,
    handleBaseUrlChange,
    isBaseUrlUpdating,
    apiKey,
    handleApiKeyChange,
    isApiKeyUpdating,
    model,
    handleModelChange,
    modelOptions,
    isModelUpdating,
    isFetchingModels,
    handleProviderSelect,
    handleModelSelect,
    handleModelCreate,
    handleRefreshModels,
  };
};
const PostProcessingSettingsApiComponent: React.FC = () => {
  const { t } = useTranslation();
  const state = usePostProcessProviderState();

  return (
    <>
      <SettingContainer
        title={t("settings.postProcessing.api.provider.title")}
        description={t("settings.postProcessing.api.provider.description")}
        descriptionMode="tooltip"
        layout="horizontal"
        grouped={true}
      >
        <div className="flex items-center gap-2">
          <ProviderSelect
            options={state.providerOptions}
            value={state.selectedProviderId}
            onChange={state.handleProviderSelect}
          />
        </div>
      </SettingContainer>

      {state.selectedProvider?.id === "custom" && (
        <SettingContainer
          title={t("settings.postProcessing.api.baseUrl.title")}
          description={t("settings.postProcessing.api.baseUrl.description")}
          descriptionMode="tooltip"
          layout="horizontal"
          grouped={true}
        >
          <div className="flex items-center gap-2">
            <BaseUrlField
              value={state.baseUrl}
              onBlur={state.handleBaseUrlChange}
              placeholder={t("settings.postProcessing.api.baseUrl.placeholder")}
              disabled={state.isBaseUrlUpdating}
              className="min-w-[380px]"
            />
          </div>
        </SettingContainer>
      )}

      <SettingContainer
        title={t("settings.postProcessing.api.apiKey.title")}
        description={t("settings.postProcessing.api.apiKey.description")}
        descriptionMode="tooltip"
        layout="horizontal"
        grouped={true}
      >
        <div className="flex items-center gap-2">
          <ApiKeyField
            value={state.apiKey}
            onBlur={state.handleApiKeyChange}
            placeholder={t("settings.postProcessing.api.apiKey.placeholder")}
            disabled={state.isApiKeyUpdating}
            className="min-w-[320px]"
          />
        </div>
      </SettingContainer>

      <SettingContainer
        title={t("settings.postProcessing.api.model.title")}
        description={
          state.isCustomProvider
            ? t("settings.postProcessing.api.model.descriptionCustom")
            : t("settings.postProcessing.api.model.descriptionDefault")
        }
        descriptionMode="tooltip"
        layout="stacked"
        grouped={true}
      >
        <div className="flex items-center gap-2">
          <ModelSelect
            value={state.model}
            options={state.modelOptions}
            disabled={state.isModelUpdating}
            isLoading={state.isFetchingModels}
            placeholder={
              state.modelOptions.length > 0
                ? t("settings.postProcessing.api.model.placeholderWithOptions")
                : t("settings.postProcessing.api.model.placeholderNoOptions")
            }
            onSelect={state.handleModelSelect}
            onCreate={state.handleModelCreate}
            onBlur={() => {}}
            className="flex-1 min-w-[380px]"
          />
          <ResetButton
            onClick={state.handleRefreshModels}
            disabled={state.isFetchingModels}
            ariaLabel={t("settings.postProcessing.api.model.refreshModels")}
            className="flex h-10 w-10 items-center justify-center"
          >
            <RefreshCcw
              className={`h-4 w-4 ${state.isFetchingModels ? "animate-spin" : ""}`}
            />
          </ResetButton>
        </div>
      </SettingContainer>
    </>
  );
};
export const PostProcessingProviderSettings = React.memo(
  PostProcessingSettingsApiComponent,
);
PostProcessingProviderSettings.displayName = "PostProcessingSettingsApi";
