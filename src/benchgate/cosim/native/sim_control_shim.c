/**
 * Host-side ctypes entry points for firmware control.c (no HAL).
 */
#include "control.h"

#include <string.h>

static converter_state_t s_state;
static telemetry_t s_tel;

void sim_control_init(void)
{
    memset(&s_state, 0, sizeof(s_state));
    memset(&s_tel, 0, sizeof(s_tel));
    control_init();
}

void sim_control_reset(void)
{
    control_reset();
}

void sim_control_set_mode(int mode)
{
    s_state.mode = (control_mode_t)mode;
}

void sim_control_set_stage(int stage)
{
    s_state.stage = (stage_mode_t)stage;
}

void sim_control_set_vset(float v)
{
    s_state.v_set_v = v;
}

void sim_control_set_iset(float a)
{
    s_state.i_set_a = a;
}

void sim_control_set_enable(int enable)
{
    s_state.enable = enable ? 1U : 0U;
}

void sim_control_set_fault(int fault)
{
    s_state.fault_latched = fault ? 1U : 0U;
}

void sim_control_update(float vin_v, float vout_v, float iout_a, float temp_c)
{
    s_tel.vin_v = vin_v;
    s_tel.vout_v = vout_v;
    s_tel.iout_a = iout_a;
    s_tel.temp_c = temp_c;
    control_update(&s_tel, &s_state);
    s_tel.duty = control_get_duty();
}

float sim_control_get_duty(void)
{
    return control_get_duty();
}

float sim_control_get_vout(void)
{
    return s_tel.vout_v;
}
