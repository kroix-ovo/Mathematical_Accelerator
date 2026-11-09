#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "codec.h"

static PyObject *crc16(PyObject *self, PyObject *args) {
    const uint8_t *data;
    Py_ssize_t n;
    unsigned int seed = 0xffff;
    (void)self;
    if (!PyArg_ParseTuple(args, "y#|I", &data, &n, &seed)) return NULL;
    return PyLong_FromUnsignedLong(ma_crc16(data, (size_t)n, (uint16_t)seed));
}

static PyObject *codec(PyObject *args, int encode) {
    const uint8_t *data;
    uint8_t *out;
    Py_ssize_t n;
    size_t cap, used = 0;
    int error;
    PyObject *result;
    if (!PyArg_ParseTuple(args, "y#", &data, &n)) return NULL;
    if (encode && n > PY_SSIZE_T_MAX - n / 254 - 1) return PyErr_NoMemory();
    cap = encode ? (size_t)n + (size_t)n / 254 + 1 : (size_t)n;
    out = PyMem_Malloc(cap ? cap : 1);
    if (out == NULL) return PyErr_NoMemory();
    error = encode ? ma_encode(data, (size_t)n, out, cap, &used)
                   : ma_decode(data, (size_t)n, out, cap, &used);
    if (error) {
        const char *message = error == 1 ? "zero byte inside COBS frame" :
                              error == 2 ? "COBS block overruns frame" :
                              error == 3 ? "zero byte inside COBS block" : "output capacity exceeded";
        PyMem_Free(out);
        PyErr_SetString(PyExc_ValueError, message);
        return NULL;
    }
    result = PyBytes_FromStringAndSize((const char *)out, (Py_ssize_t)used);
    PyMem_Free(out);
    return result;
}

static PyObject *encode(PyObject *self, PyObject *args) { (void)self; return codec(args, 1); }
static PyObject *decode(PyObject *self, PyObject *args) { (void)self; return codec(args, 0); }
static PyMethodDef methods[] = {
    {"crc16", crc16, METH_VARARGS, "CCITT-FALSE update with an optional seed."},
    {"cobs_encode", encode, METH_VARARGS, "Encode one COBS frame without delimiter."},
    {"cobs_decode", decode, METH_VARARGS, "Validate and decode one COBS frame."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_framing_native", NULL, -1, methods,
                                  NULL, NULL, NULL, NULL};
PyMODINIT_FUNC PyInit__framing_native(void) {
    PyObject *m = PyModule_Create(&module);
    if (m == NULL) return NULL;
    if (PyModule_AddStringConstant(m, "backend", "c") < 0) { Py_DECREF(m); return NULL; }
    return m;
}
