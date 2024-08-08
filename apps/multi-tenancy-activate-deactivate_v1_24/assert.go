package main

import (
	"fmt"
	"reflect"
	"strings"
)

func requireNil(object interface{}) {
	if !isNil(object) {
		panic(fmt.Sprintf("Expected nil, but got: %#v", object))
	}
}

func requireNotNil(object interface{}) {
	if isNil(object) {
		panic("Expected value not to be nil.")
	}
}

func requireTrue(val bool, msg ...string) {
	if !val {
		panic(fmt.Sprintf("Expected true: %v", msg))
	}
}

func requireContains(str, substr string) {
	if !strings.Contains(str, substr) {
		panic(fmt.Sprintf("Expected to contain: %s in %s", substr, str))
	}
}

func isNil(object interface{}) bool {
	if object == nil {
		return true
	}

	value := reflect.ValueOf(object)
	kind := value.Kind()
	isNilableKind := containsKind(
		[]reflect.Kind{
			reflect.Chan, reflect.Func,
			reflect.Interface, reflect.Map,
			reflect.Ptr, reflect.Slice, reflect.UnsafePointer},
		kind)

	if isNilableKind && value.IsNil() {
		return true
	}

	return false
}

func containsKind(kinds []reflect.Kind, kind reflect.Kind) bool {
	for i := 0; i < len(kinds); i++ {
		if kind == kinds[i] {
			return true
		}
	}

	return false
}
