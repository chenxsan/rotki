<script setup lang="ts">
import ConfirmDialog from '@/components/dialogs/ConfirmDialog.vue';
import DateDisplay from '@/components/display/DateDisplay.vue';
import Fragment from '@/components/helper/Fragment';
import MenuTooltipButton from '@/components/helper/MenuTooltipButton.vue';
import FileUpload from '@/components/import/FileUpload.vue';
import SyncButtons from '@/components/status/sync/SyncButtons.vue';

import { useSnapshotApi } from '@/services/settings/snapshot-api';
import {
  SYNC_DOWNLOAD,
  SYNC_UPLOAD,
  type SyncAction
} from '@/services/types-api';
import { useBalancesStore } from '@/store/balances';
import { type AllBalancePayload } from '@/store/balances/types';
import { useMessageStore } from '@/store/message';
import { useSessionStore } from '@/store/session';
import { usePeriodicStore } from '@/store/session/periodic';
import { useSyncStoreStore } from '@/store/session/sync-store';
import { useTasks } from '@/store/tasks';
import { type Writeable } from '@/types';
import { TaskType } from '@/types/task-type';
import { startPromise } from '@/utils';

const { t, tc } = useI18n();
const { logout } = useSessionStore();
const { lastBalanceSave, lastDataUpload } = storeToRefs(usePeriodicStore());
const { forceSync } = useSyncStoreStore();

const { fetchBalances } = useBalancesStore();
const { currentBreakpoint } = useTheme();
const premium = usePremium();
const { appSession } = useInterop();

const pending = ref<boolean>(false);
const confirmChecked = ref<boolean>(false);
const ignoreErrors = ref<boolean>(false);
const visible = ref<boolean>(false);
const syncAction = ref<SyncAction>(SYNC_UPLOAD);
const displayConfirmation = ref<boolean>(false);
const balanceSnapshotUploader = ref<any>(null);
const balanceSnapshotFile = ref<File | null>(null);
const locationDataSnapshotUploader = ref<any>(null);
const locationDataSnapshotFile = ref<File | null>(null);
const importSnapshotLoading = ref<boolean>(false);
const importSnapshotDialog = ref<boolean>(false);

const xsOnly = computed(() => get(currentBreakpoint).xsOnly);

const isDownload = computed<boolean>(() => get(syncAction) === SYNC_DOWNLOAD);
const textChoice = computed<number>(() =>
  get(syncAction) === SYNC_UPLOAD ? 1 : 2
);
const message = computed<string>(() => {
  return get(syncAction) === SYNC_UPLOAD
    ? t('sync_indicator.upload_confirmation.message_upload').toString()
    : t('sync_indicator.upload_confirmation.message_download').toString();
});

const refreshAllAndSave = async () => {
  set(visible, false);
  const payload: Writeable<Partial<AllBalancePayload>> = {
    ignoreCache: true,
    saveData: true
  };
  if (get(ignoreErrors)) {
    payload.ignoreErrors = true;
  }
  await fetchBalances(payload);
};

const showConfirmation = (action: SyncAction) => {
  set(visible, false);
  set(syncAction, action);
  set(displayConfirmation, true);
};

const performSync = async () => {
  set(pending, true);
  await forceSync(syncAction, logout);
  set(pending, false);
};

const { isTaskRunning } = useTasks();
const isSyncing = isTaskRunning(TaskType.FORCE_SYNC);

watch(isSyncing, (current, prev) => {
  if (current !== prev && !current) {
    set(displayConfirmation, false);
    set(confirmChecked, false);
  }
});

const cancel = () => {
  set(displayConfirmation, false);
  set(confirmChecked, false);
};

const importFilesCompleted = computed<boolean>(
  () => !!get(balanceSnapshotFile) && !!get(locationDataSnapshotFile)
);

const { setMessage } = useMessageStore();

const api = useSnapshotApi();

const importSnapshot = async () => {
  if (!get(importFilesCompleted)) return;
  set(importSnapshotLoading, true);

  let success = false;
  let message = '';
  try {
    if (appSession) {
      await api.importBalancesSnapshot(
        get(balanceSnapshotFile)!.path,
        get(locationDataSnapshotFile)!.path
      );
    } else {
      await api.uploadBalancesSnapshot(
        get(balanceSnapshotFile)!,
        get(locationDataSnapshotFile)!
      );
    }
    success = true;
  } catch (e: any) {
    message = e.message;
  }

  if (!success) {
    setMessage({
      title: t('sync_indicator.import_snapshot.messages.title').toString(),
      description: t(
        'sync_indicator.import_snapshot.messages.failed_description',
        {
          message
        }
      ).toString()
    });
  } else {
    setMessage({
      title: t('sync_indicator.import_snapshot.messages.title').toString(),
      description: t(
        'sync_indicator.import_snapshot.messages.success_description',
        {
          message
        }
      ).toString(),
      success: true
    });

    setTimeout(() => {
      startPromise(logout());
    }, 3000);
  }

  set(importSnapshotLoading, false);
  get(balanceSnapshotUploader)?.removeFile();
  get(locationDataSnapshotUploader)?.removeFile();
  set(balanceSnapshotFile, null);
  set(locationDataSnapshotFile, null);
};
</script>
<template>
  <fragment>
    <v-menu
      id="balances-saved-dropdown"
      v-model="visible"
      transition="slide-y-transition"
      offset-y
      :close-on-content-click="false"
      :max-width="xsOnly ? '97%' : '350px'"
      z-index="215"
    >
      <template #activator="{ on }">
        <menu-tooltip-button
          :tooltip="tc('sync_indicator.menu_tooltip', premium ? 2 : 1)"
          class-name="secondary--text text--lighten-4"
          :on-menu="on"
        >
          <v-icon> mdi-content-save </v-icon>
        </menu-tooltip-button>
      </template>
      <div>
        <div class="balance-saved-indicator__content">
          <template v-if="premium">
            <div class="font-weight-medium">
              {{ t('sync_indicator.last_data_upload') }}
            </div>
            <div class="py-2 text--secondary">
              <date-display v-if="lastDataUpload" :timestamp="lastDataUpload" />
              <span v-else>
                {{ t('sync_indicator.never_saved') }}
              </span>
            </div>
            <div>
              <sync-buttons
                :pending="pending"
                @action="showConfirmation($event)"
              />
            </div>
            <v-divider class="my-4" />
          </template>
          <div>
            <div class="font-weight-medium">
              {{ t('sync_indicator.snapshot_title') }}
            </div>
            <div class="pt-2 text--secondary">
              <date-display
                v-if="lastBalanceSave"
                :timestamp="lastBalanceSave"
              />
              <span v-else>
                {{ t('sync_indicator.never_saved') }}
              </span>
            </div>
            <v-divider class="my-4" />
            <v-row>
              <v-col>
                <v-btn color="primary" outlined @click="refreshAllAndSave()">
                  <v-icon left>mdi-content-save</v-icon>
                  {{ t('sync_indicator.force_save') }}
                </v-btn>
                <v-tooltip right max-width="300px">
                  <template #activator="{ on, attrs }">
                    <div v-bind="attrs" v-on="on">
                      <v-checkbox
                        v-model="ignoreErrors"
                        hide-details
                        label="Ignore Errors"
                      />
                    </div>
                  </template>
                  <span>{{ t('sync_indicator.ignore_errors') }}</span>
                </v-tooltip>
              </v-col>
              <v-col cols="auto" class="px-2 py-4">
                <v-tooltip bottom max-width="300px">
                  <template #activator="{ on }">
                    <v-icon v-on="on">mdi-information</v-icon>
                  </template>
                  <div>
                    {{ t('sync_indicator.snapshot_tooltip') }}
                  </div>
                </v-tooltip>
              </v-col>
            </v-row>
            <v-divider class="my-4" />
            <div>
              <div class="font-weight-medium">
                {{ t('sync_indicator.import_snapshot.title') }}
              </div>
              <div class="pt-4">
                <v-dialog
                  v-model="importSnapshotDialog"
                  max-width="600"
                  :persistent="
                    !!balanceSnapshotFile || !!locationDataSnapshotFile
                  "
                >
                  <template #activator="{ on }">
                    <v-btn color="primary" outlined v-on="on">
                      <v-icon left>mdi-import</v-icon>
                      {{ t('common.actions.import') }}
                    </v-btn>
                  </template>
                  <card>
                    <template #title>
                      {{ t('sync_indicator.import_snapshot.title') }}
                    </template>
                    <div class="pt-2">
                      <v-row>
                        <v-col>
                          <div class="font-weight-bold">
                            {{
                              t(
                                'sync_indicator.import_snapshot.balance_snapshot_file'
                              )
                            }}
                          </div>
                          <div class="py-2">
                            <file-upload
                              ref="balanceSnapshotUploader"
                              source="csv"
                              @selected="balanceSnapshotFile = $event"
                            />
                          </div>
                          <div class="text-caption">
                            {{
                              t(
                                'sync_indicator.import_snapshot.balance_snapshot_file_suggested'
                              )
                            }}
                          </div>
                        </v-col>
                        <v-col>
                          <div class="font-weight-bold">
                            {{
                              t(
                                'sync_indicator.import_snapshot.location_data_snapshot_file'
                              )
                            }}
                          </div>
                          <div class="py-2">
                            <file-upload
                              ref="locationDataSnapshotUploader"
                              source="csv"
                              @selected="locationDataSnapshotFile = $event"
                            />
                          </div>
                          <div class="text-caption">
                            {{
                              t(
                                'sync_indicator.import_snapshot.location_data_snapshot_suggested'
                              )
                            }}
                          </div>
                        </v-col>
                      </v-row>
                    </div>
                    <template #buttons>
                      <v-spacer />
                      <v-btn
                        color="primary"
                        text
                        @click="importSnapshotDialog = false"
                      >
                        {{ t('common.actions.cancel') }}
                      </v-btn>
                      <v-btn
                        color="primary"
                        :disabled="!importFilesCompleted"
                        :loading="importSnapshotLoading"
                        @click="importSnapshot"
                      >
                        {{ t('common.actions.import') }}
                      </v-btn>
                    </template>
                  </card>
                </v-dialog>
              </div>
            </div>
          </div>
        </div>
      </div>
    </v-menu>
    <confirm-dialog
      confirm-type="warning"
      :display="displayConfirmation"
      :title="tc('sync_indicator.upload_confirmation.title', textChoice)"
      :message="message"
      :disabled="!confirmChecked"
      :primary-action="
        tc('sync_indicator.upload_confirmation.action', textChoice)
      "
      :loading="isSyncing"
      :secondary-action="tc('common.actions.cancel')"
      @cancel="cancel"
      @confirm="performSync"
    >
      <div
        v-if="isDownload"
        class="font-weight-medium mt-3"
        v-text="
          t('sync_indicator.upload_confirmation.message_download_relogin')
        "
      />
      <v-checkbox
        v-model="confirmChecked"
        :label="t('sync_indicator.upload_confirmation.confirm_check')"
      />
    </confirm-dialog>
  </fragment>
</template>

<style lang="scss" scoped>
.balance-saved-indicator {
  &__content {
    width: 280px;
    padding: 16px 16px;
  }
}
</style>
