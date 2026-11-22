export const holdsViewTemplate = `
  <div v-show="tab===1" class="panel">
    <h2>占位</h2>
    <div>
      <input v-model="holdForm.slot_code" placeholder="档期编码" />
      <input v-model="holdForm.party_key" placeholder="当事人" />
      <input v-model.number="holdForm.seats" type="number" min="1" style="width:5rem" />
      <button @click="doHold">占位</button>
    </div>
    <p class="err" v-if="actionErr">{{ actionErr }}</p>
    <table>
      <thead><tr><th>ID</th><th>凭证</th><th>档</th><th>当事人</th><th>席</th><th>态</th><th>过期</th><th>倒计时</th><th>续期</th></tr></thead>
      <tbody>
        <tr v-for="h in holds" :key="h.id" @click="selectHold(h.id)" :class="{sel: detail && detail.id===h.id}">
          <td>{{ h.id }}</td><td>{{ h.credential }}</td><td>{{ h.slot_code }}</td>
          <td>{{ h.party_key }}</td><td>{{ h.seats }}</td><td>{{ h.status }}</td>
          <td>{{ h.expire_at }}</td>
          <td>{{ cd(h) }}</td>
          <td><button class="ghost" v-if="h.status==='占位中'" @click.stop="doRenew(h.id)">续期</button></td>
        </tr>
      </tbody>
    </table>

    <div v-if="detail" class="detail">
      <h3>占位详情 · {{ detail.credential }}</h3>
      <div class="kv">
        <span>当事人</span><b>{{ detail.party_key }}</b>
        <span>档期</span><b>{{ detail.slot_code }}</b>
        <span>状态</span><b>{{ detail.status }}</b>
        <span>过期时刻</span><b>{{ detail.expire_at }}</b>
        <span>倒计时</span><b>{{ cd(detail) }}</b>
        <span>续期次数</span><b>{{ detail.renew_count }}</b>
        <span>最近续期</span><b>{{ detail.last_renew_at || '—' }}</b>
      </div>
      <p class="hint">倒计时与过期时刻同源：占位行 expire_at。续期成功即改写此行并落续期审计。</p>
    </div>
  </div>
`;

export const renewViewTemplate = `
  <div v-show="tab===6" class="panel">
    <h2>续期</h2>
    <p class="err" v-if="actionErr">{{ actionErr }}</p>
    <table>
      <thead><tr><th>凭证</th><th>档</th><th>当事人</th><th>态</th><th>过期时刻</th><th>倒计时</th><th>续期次数</th><th>最近续期</th><th>续期前</th><th>续期后</th></tr></thead>
      <tbody>
        <tr v-for="r in renewals" :key="r.hold_id">
          <td>{{ r.credential }}</td><td>{{ r.slot_code }}</td><td>{{ r.party_key }}</td>
          <td>{{ r.status }}</td><td>{{ r.expire_at }}</td><td>{{ cd(r) }}</td>
          <td>{{ r.renew_count }}</td><td>{{ r.last_renew_at || '—' }}</td>
          <td>{{ r.last_old_expire || '—' }}</td><td>{{ r.last_new_expire || '—' }}</td>
        </tr>
      </tbody>
    </table>
    <h3>续期审计</h3>
    <table>
      <thead><tr><th>审计#</th><th>凭证</th><th>档</th><th>续期前</th><th>续期后</th><th>分钟</th><th>时</th></tr></thead>
      <tbody>
        <tr v-for="a in renewAudits" :key="a.audit_id">
          <td>{{ a.audit_id }}</td><td>{{ a.credential }}</td><td>{{ a.slot_code }}</td>
          <td>{{ a.old_expire }}</td><td>{{ a.new_expire }}</td>
          <td>{{ a.extend_minutes }}</td><td>{{ a.audit_at }}</td>
        </tr>
      </tbody>
    </table>
    <p class="hint">五处同源：此页倒计时、占位详情、占位行 expire_at、续期审计、对账灯与档期盘。</p>
  </div>
`;
